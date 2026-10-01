# -*- coding: utf-8 -*-
"""
素材授权台 —— 领域核心（关系库 + 校验机制 + 持久任务）

核心设计约束（对应需求）：
1. blobs 表是「内容去重身份」：相同文件摘要只存一份，仅用于存储去重。
2. assets 表是「来源身份」：不同来源登记的素材是不同行，即使 content_hash 相同。
   → 相同文件摘要不意味着授权相同；licenses 挂在 asset 上，绝不挂在 blob 上。
3. 依赖链（关系库）：assets → derivatives(派生裁图) → refs(正文引用) / exports(导出物)。
4. 校验机制：发布时一次校验 + 访问时带明确 TTL 的缓存校验 + 事件驱动失效 + 周期任务兜底。
5. 撤回用途：按 (asset, use) 计算受影响引用；同一 blob 的独立来源若有有效许可，绝不受影响。
6. 提醒由 tasks 表持久任务产生；dedup_key 唯一约束保证 sweeper 重试/重启不重复生成待办。
7. 系统只记录事实（区间、凭证、分发次数），不自动推断法律结论。
"""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- 常量

USE_CODES = ["web_display", "featured", "download", "print", "derivative", "commercial"]

# 访问时校验缓存的明确时限（秒）。精选页更短，保证“及时停止引用”。
TTL_DEFAULT_SECONDS = 300
TTL_FEATURED_SECONDS = 60

# 到期提醒窗口：授权剩余不足该天数时产生持久提醒任务
EXPIRY_REMINDER_DAYS = 7


class ConflictError(Exception):
    """并发冲突 / 状态冲突（HTTP 409）"""


class NotFoundError(Exception):
    """对象不存在（HTTP 404）"""


class ValidationError(Exception):
    """业务校验失败（HTTP 422）"""


def _uid(prefix):
    return "%s-%s" % (prefix, uuid.uuid4().hex[:12])


def iso(dt):
    """统一时间格式（UTC、秒精度）。所有写库时间必须经此函数，保证字典序==时间序。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse(s):
    if s is None:
        return None
    return datetime.fromisoformat(s)


SCHEMA = """
PRAGMA foreign_keys = ON;

-- 内容去重身份：仅按文件摘要去重存储，不承载任何授权语义
CREATE TABLE IF NOT EXISTS blobs(
  content_hash TEXT PRIMARY KEY,
  byte_size    INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL
);

-- 来源身份：每次来源登记一行；相同 hash 不同来源 = 不同 asset
CREATE TABLE IF NOT EXISTS assets(
  id           TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  content_hash TEXT NOT NULL REFERENCES blobs(content_hash),
  source_name  TEXT NOT NULL,
  source_ref   TEXT,
  status       TEXT NOT NULL DEFAULT 'active',   -- active / withdrawn
  version      INTEGER NOT NULL DEFAULT 1,        -- 乐观锁：并发替图
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

-- 授权记录：挂在 asset（来源身份）上，与 blob（去重身份）分离
CREATE TABLE IF NOT EXISTS licenses(
  id            TEXT PRIMARY KEY,
  asset_id      TEXT NOT NULL REFERENCES assets(id),
  author        TEXT NOT NULL,                    -- 作者（编辑页填写）
  evidence_type TEXT,                             -- 凭证类型（授权书/邮件/购买记录…）
  evidence_uri  TEXT,                             -- 凭证位置；NULL = 来源证据缺失
  valid_from    TEXT NOT NULL,                    -- 授权区间起（后台维护）
  valid_until   TEXT,                             -- 授权区间止；NULL = 长期
  status        TEXT NOT NULL DEFAULT 'active',   -- active / expired
  note          TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

-- 允许用途：一条授权可含多个用途；撤回按 (license, use) 粒度
CREATE TABLE IF NOT EXISTS license_uses(
  license_id    TEXT NOT NULL REFERENCES licenses(id),
  use_code      TEXT NOT NULL,
  revoked_at    TEXT,
  revoke_reason TEXT,
  PRIMARY KEY(license_id, use_code)
);

-- 派生裁图：依赖原素材，授权链向上追溯到 parent asset
CREATE TABLE IF NOT EXISTS derivatives(
  id              TEXT PRIMARY KEY,
  parent_asset_id TEXT NOT NULL REFERENCES assets(id),
  content_hash    TEXT NOT NULL REFERENCES blobs(content_hash),
  transform       TEXT,
  created_at      TEXT NOT NULL
);

-- 正文引用：页面/精选位引用素材或裁图；发布时校验，撤下是粘性状态
CREATE TABLE IF NOT EXISTS refs(
  id              TEXT PRIMARY KEY,
  page_slug       TEXT NOT NULL,
  page_kind       TEXT NOT NULL,                  -- article / featured
  use_code        TEXT NOT NULL,                  -- 该引用依赖的用途
  target_kind     TEXT NOT NULL,                  -- asset / derivative
  target_id       TEXT NOT NULL,
  state           TEXT NOT NULL DEFAULT 'published',  -- published / taken_down
  published_at    TEXT NOT NULL,
  taken_down_at   TEXT,
  takedown_reason TEXT
);

-- 导出物：记录生成时授权快照；已分发次数用于解释无法远程收回的范围
CREATE TABLE IF NOT EXISTS exports(
  id             TEXT PRIMARY KEY,
  kind           TEXT NOT NULL,                   -- pdf / zip / page_snapshot
  state          TEXT NOT NULL,                   -- running / done / stale / needs_review / regenerated
  items          TEXT NOT NULL,                   -- JSON [{target_kind,target_id,use_code}]
  snapshot       TEXT,                            -- JSON：启动时各目标授权判定快照
  started_at     TEXT NOT NULL,
  finished_at    TEXT,
  download_count INTEGER NOT NULL DEFAULT 0,
  cdn_keys       TEXT                             -- JSON array
);

-- 访问时校验缓存：明确 TTL；事件驱动失效不等 TTL
CREATE TABLE IF NOT EXISTS validation_cache(
  cache_key   TEXT PRIMARY KEY,
  payload     TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  ttl_seconds INTEGER NOT NULL,
  expires_at  TEXT NOT NULL
);

-- 持久任务/待办：dedup_key 唯一 → 重试幂等
CREATE TABLE IF NOT EXISTS tasks(
  id         TEXT PRIMARY KEY,
  dedup_key  TEXT UNIQUE,
  kind       TEXT NOT NULL,        -- license_expiring / evidence_missing / regenerate_export / export_review
  payload    TEXT NOT NULL,
  state      TEXT NOT NULL DEFAULT 'pending',  -- pending / done / cancelled
  attempts   INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- CDN 刷新记录：撤下后旧对象可能仍在缓存，需显式 purge 并跟踪确认
CREATE TABLE IF NOT EXISTS cdn_purges(
  id           TEXT PRIMARY KEY,
  cdn_key      TEXT NOT NULL,
  reason       TEXT,
  state        TEXT NOT NULL DEFAULT 'pending',  -- pending / confirmed
  requested_at TEXT NOT NULL,
  confirmed_at TEXT
);

-- 审计：只记录事实，不下法律结论
CREATE TABLE IF NOT EXISTS audit_log(
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  at      TEXT NOT NULL,
  actor   TEXT NOT NULL,
  action  TEXT NOT NULL,
  subject TEXT NOT NULL,
  detail  TEXT NOT NULL
);
"""


class LicensingStore:
    def __init__(self, db_path=":memory:"):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ------------------------------------------------------------ 基础工具

    def _audit(self, actor, action, subject, detail, now):
        self.db.execute(
            "INSERT INTO audit_log(at,actor,action,subject,detail) VALUES(?,?,?,?,?)",
            (iso(now), actor, action, subject, json.dumps(detail, ensure_ascii=False)),
        )

    def _ensure_blob(self, content_hash, byte_size, now):
        """内容去重：相同摘要只登记一次。注意：这里不产生任何授权身份。"""
        self.db.execute(
            "INSERT OR IGNORE INTO blobs(content_hash,byte_size,created_at) VALUES(?,?,?)",
            (content_hash, byte_size or 0, iso(now)),
        )

    def _get(self, sql, args=()):
        return self.db.execute(sql, args).fetchone()

    def _all(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    # ------------------------------------------------------------ 素材（来源身份）

    def register_asset(self, title, content_hash, source_name, now,
                       byte_size=0, source_ref=None, actor="console"):
        """登记素材。相同 content_hash 的不同来源会生成不同的 asset 行。"""
        self._ensure_blob(content_hash, byte_size, now)
        aid = _uid("asset")
        self.db.execute(
            "INSERT INTO assets(id,title,content_hash,source_name,source_ref,status,version,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'active',1,?,?)",
            (aid, title, content_hash, source_name, source_ref, iso(now), iso(now)),
        )
        self._audit(actor, "asset.register", aid,
                    {"title": title, "content_hash": content_hash, "source_name": source_name,
                     "note": "去重身份(blob)与授权身份(asset)分离：同摘要不共享授权"}, now)
        self.db.commit()
        return aid

    def replace_asset_file(self, asset_id, new_content_hash, expected_version, now,
                           byte_size=0, actor="console"):
        """并发替图：乐观锁 CAS。两个并发替图只有一个成功，另一个 409。"""
        row = self._get("SELECT * FROM assets WHERE id=?", (asset_id,))
        if not row:
            raise NotFoundError("素材不存在: %s" % asset_id)
        self._ensure_blob(new_content_hash, byte_size, now)
        cur = self.db.execute(
            "UPDATE assets SET content_hash=?, version=version+1, updated_at=?"
            " WHERE id=? AND version=? AND status='active'",
            (new_content_hash, iso(now), asset_id, expected_version),
        )
        if cur.rowcount == 0:
            fresh = self._get("SELECT version FROM assets WHERE id=?", (asset_id,))
            raise ConflictError(
                "替图冲突：期望版本 %s，当前版本 %s（可能有并发替图），请刷新后重试"
                % (expected_version, fresh["version"] if fresh else "?"))
        new_ver = expected_version + 1
        old_key = "asset:%s:v%d" % (asset_id, expected_version)
        self._request_purge(old_key, "替图后旧版本对象需失效", now)
        self._invalidate_pages_of_asset(asset_id, now)
        self._audit(actor, "asset.replace", asset_id,
                    {"new_content_hash": new_content_hash, "from_version": expected_version,
                     "to_version": new_ver, "purged_cdn_key": old_key}, now)
        self.db.commit()
        return new_ver

    # ------------------------------------------------------------ 授权

    def create_license(self, asset_id, author, uses, valid_from, now,
                       valid_until=None, evidence_type=None, evidence_uri=None,
                       note=None, actor="console"):
        if not self._get("SELECT id FROM assets WHERE id=?", (asset_id,)):
            raise NotFoundError("素材不存在: %s" % asset_id)
        for u in uses:
            if u not in USE_CODES:
                raise ValidationError("未知用途: %s" % u)
        lid = _uid("lic")
        self.db.execute(
            "INSERT INTO licenses(id,asset_id,author,evidence_type,evidence_uri,valid_from,valid_until,status,note,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,'active',?,?,?)",
            (lid, asset_id, author, evidence_type, evidence_uri,
             iso(valid_from), iso(valid_until) if valid_until else None, note, iso(now), iso(now)),
        )
        for u in uses:
            self.db.execute("INSERT INTO license_uses(license_id,use_code) VALUES(?,?)", (lid, u))
        self._audit(actor, "license.create", lid,
                    {"asset_id": asset_id, "author": author, "uses": uses,
                     "valid_from": iso(valid_from),
                     "valid_until": iso(valid_until) if valid_until else None,
                     "evidence_present": bool(evidence_uri)}, now)
        self.db.commit()
        self.sync_reminders(now)  # 证据缺失/临期提醒立即对齐
        return lid

    def set_evidence(self, license_id, evidence_type, evidence_uri, now, actor="console"):
        lic = self._lic(license_id)
        self.db.execute("UPDATE licenses SET evidence_type=?, evidence_uri=?, updated_at=? WHERE id=?",
                        (evidence_type, evidence_uri, iso(now), license_id))
        self._cancel_tasks("evidence:%s" % license_id, "已补充来源证据", now)
        self._invalidate_pages_of_asset(lic["asset_id"], now)
        self._audit(actor, "license.evidence", license_id,
                    {"evidence_type": evidence_type, "evidence_uri": evidence_uri}, now)
        self.db.commit()

    def renew_license(self, license_id, new_valid_until, now, actor="console"):
        """授权续期：延长区间。已产生的临期提醒自动取消；缓存失效以便重新判定。"""
        lic = self._lic(license_id)
        if new_valid_until <= now:
            raise ValidationError("续期后的 valid_until 必须晚于当前时间")
        self.db.execute("UPDATE licenses SET valid_until=?, status='active', updated_at=? WHERE id=?",
                        (iso(new_valid_until), iso(now), license_id))
        self._cancel_tasks("expiring:%s" % license_id, "授权已续期", now)
        self._invalidate_pages_of_asset(lic["asset_id"], now)
        self._audit(actor, "license.renew", license_id,
                    {"asset_id": lic["asset_id"], "new_valid_until": iso(new_valid_until)}, now)
        self.db.commit()

    def revoke_use(self, license_id, use_code, reason, now, actor="console", preview=False):
        """撤回某用途：先计算影响（dry-run 可预览），再执行撤下与重新生成。
        关键：只影响「该 asset 授权链」上的引用；同摘要的独立来源若有有效许可不受影响。"""
        lic = self._lic(license_id)
        use = self._get("SELECT * FROM license_uses WHERE license_id=? AND use_code=?",
                        (license_id, use_code))
        if not use:
            raise NotFoundError("该授权不包含用途 %s" % use_code)
        if use["revoked_at"]:
            raise ConflictError("用途 %s 已被撤回过" % use_code)
        if preview:
            # 预演：假设该 (license, use) 被撤回后的影响，不落库
            return self.compute_impact(lic["asset_id"], use_code, now,
                                       excluding_license_id=license_id)
        self.db.execute("UPDATE license_uses SET revoked_at=?, revoke_reason=? WHERE license_id=? AND use_code=?",
                        (iso(now), reason, license_id, use_code))
        self._audit(actor, "license.revoke_use", license_id,
                    {"use_code": use_code, "reason": reason, "asset_id": lic["asset_id"]}, now)
        impact = self.compute_impact(lic["asset_id"], use_code, now)
        applied = self._apply_coverage_loss(lic["asset_id"], use_code, now,
                                            cause="用途撤回: %s" % reason, actor=actor)
        self.db.commit()
        impact["applied"] = applied
        return impact

    def compute_impact(self, asset_id, use_code, now, excluding_license_id=None):
        """影响分析：若该 asset 仍有其他有效授权覆盖该用途 → 无影响（不误删独立来源）。
        excluding_license_id：预演模式下，假设该授权已不可用。"""
        if self.verdict_for_asset(asset_id, use_code, now,
                                  excluding_license_id=excluding_license_id)["verdict"] == "allow":
            return {"covered_elsewhere": True, "affected_refs": [], "affected_exports": [],
                    "note": "该素材仍有其他有效授权覆盖用途 %s，引用不受影响" % use_code}
        targets = self._target_closure(asset_id)
        refs, exports = [], []
        for tk, tid in targets:
            for r in self._all("SELECT * FROM refs WHERE target_kind=? AND target_id=? AND use_code=? AND state='published'",
                               (tk, tid, use_code)):
                refs.append(dict(r))
        for e in self._all("SELECT * FROM exports WHERE state IN ('running','done')"):
            items = json.loads(e["items"])
            if any((it["target_kind"], it["target_id"]) in targets and it["use_code"] == use_code
                   for it in items):
                exports.append({"id": e["id"], "state": e["state"], "kind": e["kind"],
                                "download_count": e["download_count"]})
        return {"covered_elsewhere": False, "affected_refs": refs, "affected_exports": exports}

    # ------------------------------------------------------------ 校验（发布时 + 访问时缓存）

    def verdict_for_asset(self, asset_id, use_code, now, excluding_license_id=None):
        """对 (asset, use, now) 给出判定。多个授权记录任一有效即允许。
        excluding_license_id：判定时忽略该授权（用于撤回预演）。"""
        asset = self._get("SELECT * FROM assets WHERE id=?", (asset_id,))
        if not asset:
            return {"verdict": "deny", "reason": "素材不存在"}
        if asset["status"] != "active":
            return {"verdict": "deny", "reason": "素材已下线"}
        lics = self._all("SELECT * FROM licenses WHERE asset_id=? AND status='active'", (asset_id,))
        if excluding_license_id:
            lics = [l for l in lics if l["id"] != excluding_license_id]
        if not lics:
            return {"verdict": "deny", "reason": "无授权记录"}
        saw_interval_miss = saw_use_missing = saw_revoked = saw_evidence = False
        for lic in lics:
            vf, vu = parse(lic["valid_from"]), parse(lic["valid_until"])
            if not (vf <= now and (vu is None or now <= vu)):
                saw_interval_miss = True
                continue
            use = self._get("SELECT * FROM license_uses WHERE license_id=? AND use_code=?",
                            (lic["id"], use_code))
            if not use:
                saw_use_missing = True
                continue
            if use["revoked_at"]:
                saw_revoked = True
                continue
            if not lic["evidence_uri"]:
                saw_evidence = True
                continue
            return {"verdict": "allow", "license_id": lic["id"], "author": lic["author"],
                    "valid_until": lic["valid_until"]}
        if saw_evidence:
            reason = "来源证据缺失"
        elif saw_revoked:
            reason = "用途已被撤回"
        elif saw_interval_miss:
            reason = "授权区间不覆盖当前时间"
        elif saw_use_missing:
            reason = "用途未在授权中"
        else:
            reason = "无有效授权"
        return {"verdict": "deny", "reason": reason}

    def _resolve_asset(self, target_kind, target_id):
        """依赖链追溯：裁图 → 原素材。"""
        if target_kind == "asset":
            return target_id
        d = self._get("SELECT * FROM derivatives WHERE id=?", (target_id,))
        if not d:
            raise NotFoundError("派生裁图不存在: %s" % target_id)
        return d["parent_asset_id"]

    def publish_ref(self, page_slug, page_kind, use_code, target_kind, target_id, now, actor="editor"):
        """发布时一次校验：不通过则拒绝发布（同步、强一致）。"""
        asset_id = self._resolve_asset(target_kind, target_id)
        v = self.verdict_for_asset(asset_id, use_code, now)
        if v["verdict"] != "allow":
            self._audit(actor, "ref.publish_denied", target_id,
                        {"page_slug": page_slug, "use_code": use_code, "reason": v["reason"]}, now)
            self.db.commit()
            raise ValidationError("发布时校验未通过: %s" % v["reason"])
        rid = _uid("ref")
        self.db.execute(
            "INSERT INTO refs(id,page_slug,page_kind,use_code,target_kind,target_id,state,published_at)"
            " VALUES(?,?,?,?,?,?,'published',?)",
            (rid, page_slug, page_kind, use_code, target_kind, target_id, iso(now)))
        self._invalidate_page(page_slug)
        self._audit(actor, "ref.publish", rid,
                    {"page_slug": page_slug, "page_kind": page_kind, "use_code": use_code,
                     "target": "%s/%s" % (target_kind, target_id), "license_id": v.get("license_id")}, now)
        self.db.commit()
        return rid

    def republish_ref(self, ref_id, now, actor="editor"):
        """撤下是粘性状态：续期/补证据后需显式重新发布，并重新通过发布时校验。"""
        r = self._get("SELECT * FROM refs WHERE id=?", (ref_id,))
        if not r:
            raise NotFoundError("引用不存在: %s" % ref_id)
        if r["state"] == "published":
            return
        asset_id = self._resolve_asset(r["target_kind"], r["target_id"])
        v = self.verdict_for_asset(asset_id, r["use_code"], now)
        if v["verdict"] != "allow":
            raise ValidationError("重新发布校验未通过: %s" % v["reason"])
        self.db.execute("UPDATE refs SET state='published', taken_down_at=NULL, takedown_reason=NULL WHERE id=?",
                        (ref_id,))
        self._invalidate_page(r["page_slug"])
        self._audit(actor, "ref.republish", ref_id, {"page_slug": r["page_slug"]}, now)
        self.db.commit()

    def render_page(self, page_slug, now):
        """访问时持续校验（带明确 TTL 的缓存）：
        - 缓存命中且未过期 → 直接返回（避免每次访问全量重算）；
        - 失效/过期 → 重新逐条判定。事件驱动失效保证受限内容不等 TTL 即被拦。"""
        key = "render:%s" % page_slug
        hit = self._get("SELECT * FROM validation_cache WHERE cache_key=?", (key,))
        if hit and parse(hit["expires_at"]) > now:
            out = json.loads(hit["payload"])
            out["cache"] = {"hit": True, "ttl_seconds": hit["ttl_seconds"], "expires_at": hit["expires_at"]}
            return out
        refs = self._all("SELECT * FROM refs WHERE page_slug=? AND state='published'", (page_slug,))
        down = self._all("SELECT * FROM refs WHERE page_slug=? AND state='taken_down'", (page_slug,))
        visible, blocked = [], []
        taken_down = [{"ref_id": r["id"], "target": "%s/%s" % (r["target_kind"], r["target_id"]),
                       "use_code": r["use_code"], "reason": r["takedown_reason"]} for r in down]
        featured = any(r["page_kind"] == "featured" for r in refs)
        for r in refs:
            asset_id = self._resolve_asset(r["target_kind"], r["target_id"])
            v = self.verdict_for_asset(asset_id, r["use_code"], now)
            item = {"ref_id": r["id"], "target": "%s/%s" % (r["target_kind"], r["target_id"]),
                    "use_code": r["use_code"], "page_kind": r["page_kind"]}
            if v["verdict"] == "allow":
                item["license_id"] = v.get("license_id")
                visible.append(item)
            else:
                item["reason"] = v["reason"]
                blocked.append(item)
        ttl = TTL_FEATURED_SECONDS if featured else TTL_DEFAULT_SECONDS
        payload = {"page_slug": page_slug, "visible": visible, "blocked": blocked,
                   "taken_down": taken_down, "generated_at": iso(now)}
        self.db.execute(
            "INSERT INTO validation_cache(cache_key,payload,computed_at,ttl_seconds,expires_at) VALUES(?,?,?,?,?)"
            " ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, computed_at=excluded.computed_at,"
            " ttl_seconds=excluded.ttl_seconds, expires_at=excluded.expires_at",
            (key, json.dumps(payload, ensure_ascii=False), iso(now), ttl,
             iso(now + timedelta(seconds=ttl))))
        self.db.commit()
        out = dict(payload)
        out["cache"] = {"hit": False, "ttl_seconds": ttl,
                        "expires_at": iso(now + timedelta(seconds=ttl))}
        return out

    # ------------------------------------------------------------ 导出物

    def start_export(self, kind, items, now, actor="editor"):
        """启动导出：记录授权快照。若启动即不合法 → 拒绝。"""
        snapshot = {}
        for it in items:
            asset_id = self._resolve_asset(it["target_kind"], it["target_id"])
            v = self.verdict_for_asset(asset_id, it["use_code"], now)
            snapshot["%s/%s:%s" % (it["target_kind"], it["target_id"], it["use_code"])] = v
        bad = {k: v for k, v in snapshot.items() if v["verdict"] != "allow"}
        if bad:
            raise ValidationError("导出启动校验未通过: %s" % json.dumps(bad, ensure_ascii=False))
        eid = _uid("exp")
        self.db.execute(
            "INSERT INTO exports(id,kind,state,items,snapshot,started_at,cdn_keys) VALUES(?,?,?,?,?,?,?)",
            (eid, kind, "running", json.dumps(items, ensure_ascii=False),
             json.dumps(snapshot, ensure_ascii=False), iso(now), "[]"))
        self._audit(actor, "export.start", eid, {"kind": kind, "items": items}, now)
        self.db.commit()
        return eid

    def finish_export(self, export_id, now, actor="system"):
        """完成导出：重新校验。若期间授权到期/撤回 → 标记 needs_review，不分发。
        快照仍保留，可解释「启动时刻是合法的」这一事实。"""
        e = self._export(export_id)
        if e["state"] != "running":
            raise ConflictError("导出不在运行态: %s" % e["state"])
        items = json.loads(e["items"])
        bad = {}
        for it in items:
            asset_id = self._resolve_asset(it["target_kind"], it["target_id"])
            v = self.verdict_for_asset(asset_id, it["use_code"], now)
            if v["verdict"] != "allow":
                bad["%s/%s:%s" % (it["target_kind"], it["target_id"], it["use_code"])] = v
        if bad:
            self.db.execute("UPDATE exports SET state='needs_review', finished_at=? WHERE id=?",
                            (iso(now), export_id))
            self._create_task("export_review", "export_review:%s" % export_id,
                              {"export_id": export_id,
                               "fact": "导出期间授权状态变化，需人工复核；启动时快照见 exports.snapshot",
                               "blocked": bad}, now)
            self._audit(actor, "export.needs_review", export_id,
                        {"fact": "导出期间授权状态变化", "blocked": bad,
                         "note": "系统只记录事实，是否可用由人工判断"}, now)
        else:
            keys = ["export:%s" % export_id]
            self.db.execute("UPDATE exports SET state='done', finished_at=?, cdn_keys=? WHERE id=?",
                            (iso(now), json.dumps(keys), export_id))
            self._audit(actor, "export.done", export_id, {"cdn_keys": keys}, now)
        self.db.commit()
        return self._export(export_id)["state"]

    def record_download(self, export_id, now):
        e = self._export(export_id)
        if e["state"] != "done":
            raise ConflictError("仅已完成的导出可下载，当前状态: %s" % e["state"])
        self.db.execute("UPDATE exports SET download_count=download_count+1 WHERE id=?", (export_id,))
        self.db.commit()

    def unrecoverable_report(self, now):
        """事实报告：哪些旧下载已分发、无法远程收回。措辞只陈述事实，不作法律结论。"""
        out = []
        for e in self._all("SELECT * FROM exports WHERE download_count>0 AND state IN ('done','stale')"):
            items = json.loads(e["items"])
            uncovered = []
            for it in items:
                asset_id = self._resolve_asset(it["target_kind"], it["target_id"])
                v = self.verdict_for_asset(asset_id, it["use_code"], now)
                if v["verdict"] != "allow":
                    uncovered.append({"target": "%s/%s" % (it["target_kind"], it["target_id"]),
                                      "use_code": it["use_code"], "reason": v["reason"]})
            if uncovered:
                out.append({
                    "export_id": e["id"], "kind": e["kind"], "state": e["state"],
                    "download_count": e["download_count"], "uncovered_items": uncovered,
                    "fact": "该导出物在受限前已被下载 %d 次；已分发副本无法通过系统远程收回。"
                            "此处仅记录事实，是否产生法律后果需人工评估。" % e["download_count"]})
        return out

    # ------------------------------------------------------------ 撤下 / 重新生成 / CDN

    def _apply_coverage_loss(self, asset_id, use_code, now, cause, actor):
        """覆盖丧失后的统一管线：撤下引用 → 失效缓存 → CDN purge → 导出物标记+重新生成任务。"""
        impact = self.compute_impact(asset_id, use_code, now)
        if impact["covered_elsewhere"]:
            self._audit(actor, "coverage.kept", asset_id,
                        {"use_code": use_code, "fact": "仍存在其他有效授权，引用未受影响"}, now)
            return {"taken_down": 0, "exports_staled": 0}
        taken_down, staled = 0, 0
        pages = set()
        for r in impact["affected_refs"]:
            self.db.execute("UPDATE refs SET state='taken_down', taken_down_at=?, takedown_reason=? WHERE id=?",
                            (iso(now), cause, r["id"]))
            pages.add(r["page_slug"])
            taken_down += 1
            self._audit(actor, "ref.takedown", r["id"],
                        {"page_slug": r["page_slug"], "use_code": use_code, "cause": cause}, now)
        for slug in pages:
            self._invalidate_page(slug)
        for key in self._cdn_keys_of_asset(asset_id):
            self._request_purge(key, cause, now)
        for ex in impact["affected_exports"]:
            if ex["state"] == "done":
                self.db.execute("UPDATE exports SET state='stale' WHERE id=?", (ex["id"],))
                self._create_task("regenerate_export", "regen:%s" % ex["id"],
                                  {"export_id": ex["id"], "cause": cause}, now)
                e = self._export(ex["id"])
                for k in json.loads(e["cdn_keys"] or "[]"):
                    self._request_purge(k, cause, now)
                if e["download_count"] > 0:
                    self._audit(actor, "export.unrecoverable", ex["id"],
                                {"fact": "该导出物在受限前已被下载 %d 次，已分发副本无法远程收回"
                                         % e["download_count"],
                                 "note": "仅记录事实，不作法律结论"}, now)
                staled += 1
            # running 的导出不处理：finish 时会重新校验并转 needs_review
        return {"taken_down": taken_down, "exports_staled": staled}

    def regenerate_export(self, export_id, now, actor="system"):
        """重新生成：按当前授权状态重建导出；仍不合法则 needs_review。"""
        old = self._export(export_id)
        if old["state"] not in ("stale", "needs_review"):
            raise ConflictError("仅 stale/needs_review 的导出可重新生成，当前: %s" % old["state"])
        items = json.loads(old["items"])
        new_id = _uid("exp")
        snapshot = {}
        bad = {}
        for it in items:
            asset_id = self._resolve_asset(it["target_kind"], it["target_id"])
            v = self.verdict_for_asset(asset_id, it["use_code"], now)
            snapshot["%s/%s:%s" % (it["target_kind"], it["target_id"], it["use_code"])] = v
            if v["verdict"] != "allow":
                bad["%s/%s:%s" % (it["target_kind"], it["target_id"], it["use_code"])] = v
        state = "needs_review" if bad else "done"
        self.db.execute(
            "INSERT INTO exports(id,kind,state,items,snapshot,started_at,finished_at,cdn_keys)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (new_id, old["kind"], state, old["items"], json.dumps(snapshot, ensure_ascii=False),
             iso(now), iso(now), json.dumps([] if bad else ["export:%s" % new_id])))
        self.db.execute("UPDATE exports SET state='regenerated' WHERE id=?", (export_id,))
        self._complete_tasks_by_prefix("regen:%s" % export_id, now)
        self._audit(actor, "export.regenerated", export_id,
                    {"new_export_id": new_id, "new_state": state,
                     "blocked": bad if bad else None}, now)
        self.db.commit()
        return new_id

    # ------------------------------------------------------------ 周期任务（兜底 + 提醒，幂等）

    def run_sweeper(self, now, actor="sweeper"):
        """周期兜底：
        1) 到期授权 → expired → 走覆盖丧失管线（到期不是事件，必须有人扫）；
        2) 临期/缺证据提醒 → 幂等生成持久任务；条件消失 → 自动取消。
        重试/重复运行安全：dedup_key 唯一约束 + INSERT OR IGNORE。"""
        expired_now = self._all(
            "SELECT * FROM licenses WHERE status='active' AND valid_until IS NOT NULL AND valid_until<=?",
            (iso(now),))
        for lic in expired_now:
            self.db.execute("UPDATE licenses SET status='expired', updated_at=? WHERE id=?",
                            (iso(now), lic["id"]))
            self._audit(actor, "license.expired", lic["id"],
                        {"asset_id": lic["asset_id"], "valid_until": lic["valid_until"]}, now)
            uses = self._all("SELECT use_code FROM license_uses WHERE license_id=? AND revoked_at IS NULL",
                             (lic["id"],))
            for u in uses:
                self._apply_coverage_loss(lic["asset_id"], u["use_code"], now,
                                          cause="授权到期", actor=actor)
        reminders = self.sync_reminders(now)
        self.db.commit()
        return {"expired": len(expired_now), "reminders": reminders}

    def sync_reminders(self, now):
        """对齐提醒任务（幂等）：返回 (新建数, 取消数)。"""
        created = cancelled = 0
        horizon = iso(now + timedelta(days=EXPIRY_REMINDER_DAYS))
        # 临期：active 且 valid_until 在 (now, now+7d]
        expiring = self._all(
            "SELECT * FROM licenses WHERE status='active' AND valid_until IS NOT NULL"
            " AND valid_until>? AND valid_until<=?", (iso(now), horizon))
        expiring_ids = set()
        for lic in expiring:
            expiring_ids.add(lic["id"])
            if self._create_task("license_expiring", "expiring:%s" % lic["id"],
                                 {"license_id": lic["id"], "asset_id": lic["asset_id"],
                                  "valid_until": lic["valid_until"],
                                  "fact": "授权将于 %s 到期" % lic["valid_until"]}, now):
                created += 1
        # 条件已消失的临期任务 → 取消（如已续期）
        for t in self._all("SELECT * FROM tasks WHERE kind='license_expiring' AND state='pending'"):
            lid = json.loads(t["payload"])["license_id"]
            if lid not in expiring_ids:
                self._cancel_task_row(t, "授权已续期或已失效，提醒关闭", now)
                cancelled += 1
        # 缺证据：active 且 evidence_uri 为空
        missing = self._all("SELECT * FROM licenses WHERE status='active' AND evidence_uri IS NULL")
        missing_ids = set()
        for lic in missing:
            missing_ids.add(lic["id"])
            if self._create_task("evidence_missing", "evidence:%s" % lic["id"],
                                 {"license_id": lic["id"], "asset_id": lic["asset_id"],
                                  "fact": "授权缺少来源证据（凭证）"}, now):
                created += 1
        for t in self._all("SELECT * FROM tasks WHERE kind='evidence_missing' AND state='pending'"):
            lid = json.loads(t["payload"])["license_id"]
            if lid not in missing_ids:
                self._cancel_task_row(t, "已补充来源证据", now)
                cancelled += 1
        self.db.commit()
        return {"created": created, "cancelled": cancelled}

    # ------------------------------------------------------------ 任务/缓存/CDN 内部件

    def _create_task(self, kind, dedup_key, payload, now):
        """幂等建任务：dedup_key 唯一，重试/重启不会产生重复待办。"""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO tasks(id,dedup_key,kind,payload,state,created_at,updated_at)"
            " VALUES(?,?,?,?,'pending',?,?)",
            (_uid("task"), dedup_key, kind, json.dumps(payload, ensure_ascii=False), iso(now), iso(now)))
        return cur.rowcount > 0

    def _cancel_tasks(self, dedup_key, reason, now):
        for t in self._all("SELECT * FROM tasks WHERE dedup_key=? AND state='pending'", (dedup_key,)):
            self._cancel_task_row(t, reason, now)

    def _cancel_task_row(self, t, reason, now):
        self.db.execute("UPDATE tasks SET state='cancelled', updated_at=? WHERE id=?", (iso(now), t["id"]))
        self._audit("system", "task.cancelled", t["id"], {"kind": t["kind"], "reason": reason}, now)

    def _complete_tasks_by_prefix(self, dedup_key, now):
        self.db.execute("UPDATE tasks SET state='done', updated_at=? WHERE dedup_key=? AND state='pending'",
                        (iso(now), dedup_key))

    def execute_task(self, task_id, now, actor="console"):
        """执行持久任务。attempts 计数；可安全重试（ regenerate 等操作本身幂等/受状态保护）。"""
        t = self._get("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not t:
            raise NotFoundError("任务不存在: %s" % task_id)
        if t["state"] != "pending":
            raise ConflictError("任务不在待处理态: %s" % t["state"])
        payload = json.loads(t["payload"])
        self.db.execute("UPDATE tasks SET attempts=attempts+1, updated_at=? WHERE id=?", (iso(now), task_id))
        result = None
        if t["kind"] == "regenerate_export":
            result = {"new_export_id": self.regenerate_export(payload["export_id"], now, actor=actor)}
        # 提醒类任务由人工处理后标记完成
        self.db.execute("UPDATE tasks SET state='done', updated_at=? WHERE id=?", (iso(now), task_id))
        self._audit(actor, "task.done", task_id, {"kind": t["kind"], "result": result}, now)
        self.db.commit()
        return result

    def _invalidate_page(self, page_slug):
        self.db.execute("DELETE FROM validation_cache WHERE cache_key=?", ("render:%s" % page_slug,))

    def _invalidate_pages_of_asset(self, asset_id, now=None):
        targets = self._target_closure(asset_id)
        slugs = set()
        for tk, tid in targets:
            for r in self._all("SELECT DISTINCT page_slug FROM refs WHERE target_kind=? AND target_id=?",
                               (tk, tid)):
                slugs.add(r["page_slug"])
        for s in slugs:
            self._invalidate_page(s)

    def _target_closure(self, asset_id):
        """依赖闭包：原素材 + 其全部派生裁图。"""
        targets = {("asset", asset_id)}
        for d in self._all("SELECT id FROM derivatives WHERE parent_asset_id=?", (asset_id,)):
            targets.add(("derivative", d["id"]))
        return targets

    def _cdn_keys_of_asset(self, asset_id):
        a = self._get("SELECT version FROM assets WHERE id=?", (asset_id,))
        keys = []
        if a:
            keys.append("asset:%s:v%d" % (asset_id, a["version"]))
        for d in self._all("SELECT id FROM derivatives WHERE parent_asset_id=?", (asset_id,)):
            keys.append("deriv:%s" % d["id"])
        return keys

    def _request_purge(self, cdn_key, reason, now):
        """登记 CDN 刷新（同一对象的待处理刷新去重）。
        注意：purge 确认前旧对象可能仍在缓存，访问层必须自行拦截，
        不能依赖 CDN 刷新作为唯一防线。"""
        dup = self._get("SELECT id FROM cdn_purges WHERE cdn_key=? AND state='pending'", (cdn_key,))
        if dup:
            return
        self.db.execute(
            "INSERT INTO cdn_purges(id,cdn_key,reason,state,requested_at) VALUES(?,?,?,'pending',?)",
            (_uid("purge"), cdn_key, reason, iso(now)))

    def confirm_purge(self, purge_id, now):
        p = self._get("SELECT * FROM cdn_purges WHERE id=?", (purge_id,))
        if not p:
            raise NotFoundError("purge 记录不存在: %s" % purge_id)
        self.db.execute("UPDATE cdn_purges SET state='confirmed', confirmed_at=? WHERE id=?",
                        (iso(now), purge_id))
        self._audit("cdn", "cdn.purge_confirmed", purge_id, {"cdn_key": p["cdn_key"]}, now)
        self.db.commit()

    def add_derivative(self, parent_asset_id, content_hash, transform, now, byte_size=0, actor="editor"):
        if not self._get("SELECT id FROM assets WHERE id=?", (parent_asset_id,)):
            raise NotFoundError("原素材不存在: %s" % parent_asset_id)
        self._ensure_blob(content_hash, byte_size, now)
        did = _uid("deriv")
        self.db.execute(
            "INSERT INTO derivatives(id,parent_asset_id,content_hash,transform,created_at) VALUES(?,?,?,?,?)",
            (did, parent_asset_id, content_hash, transform, iso(now)))
        self._audit(actor, "derivative.create", did,
                    {"parent_asset_id": parent_asset_id, "transform": transform}, now)
        self.db.commit()
        return did

    # ------------------------------------------------------------ 查询

    def _lic(self, license_id):
        lic = self._get("SELECT * FROM licenses WHERE id=?", (license_id,))
        if not lic:
            raise NotFoundError("授权不存在: %s" % license_id)
        return lic

    def _export(self, export_id):
        e = self._get("SELECT * FROM exports WHERE id=?", (export_id,))
        if not e:
            raise NotFoundError("导出不存在: %s" % export_id)
        return e

    def overview(self, now):
        return {
            "assets": self._get("SELECT COUNT(*) c FROM assets WHERE status='active'")["c"],
            "licenses_active": self._get("SELECT COUNT(*) c FROM licenses WHERE status='active'")["c"],
            "licenses_expired": self._get("SELECT COUNT(*) c FROM licenses WHERE status='expired'")["c"],
            "refs_published": self._get("SELECT COUNT(*) c FROM refs WHERE state='published'")["c"],
            "refs_taken_down": self._get("SELECT COUNT(*) c FROM refs WHERE state='taken_down'")["c"],
            "tasks_pending": self._get("SELECT COUNT(*) c FROM tasks WHERE state='pending'")["c"],
            "purges_pending": self._get("SELECT COUNT(*) c FROM cdn_purges WHERE state='pending'")["c"],
            "exports_attention": self._get("SELECT COUNT(*) c FROM exports WHERE state IN ('stale','needs_review')")["c"],
        }

    def list_assets(self):
        rows = []
        for a in self._all("SELECT * FROM assets ORDER BY created_at DESC"):
            lics = []
            for lic in self._all("SELECT * FROM licenses WHERE asset_id=? ORDER BY created_at", (a["id"],)):
                uses = [dict(u) for u in self._all("SELECT * FROM license_uses WHERE license_id=?", (lic["id"],))]
                d = dict(lic)
                d["uses"] = uses
                lics.append(d)
            d = dict(a)
            d["licenses"] = lics
            d["derivatives"] = [dict(x) for x in self._all(
                "SELECT * FROM derivatives WHERE parent_asset_id=?", (a["id"],))]
            rows.append(d)
        return rows

    def list_refs(self):
        return [dict(r) for r in self._all("SELECT * FROM refs ORDER BY published_at DESC")]

    def list_exports(self):
        return [dict(e) for e in self._all("SELECT * FROM exports ORDER BY started_at DESC")]

    def list_tasks(self, state=None):
        if state:
            return [dict(t) for t in self._all("SELECT * FROM tasks WHERE state=? ORDER BY created_at DESC", (state,))]
        return [dict(t) for t in self._all("SELECT * FROM tasks ORDER BY created_at DESC")]

    def list_purges(self):
        return [dict(p) for p in self._all("SELECT * FROM cdn_purges ORDER BY requested_at DESC")]

    def list_cache(self, now):
        out = []
        for c in self._all("SELECT * FROM validation_cache ORDER BY computed_at DESC"):
            d = dict(c)
            d["expired"] = parse(c["expires_at"]) <= now
            out.append(d)
        return out

    def list_audit(self, limit=200):
        return [dict(a) for a in self._all(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]
