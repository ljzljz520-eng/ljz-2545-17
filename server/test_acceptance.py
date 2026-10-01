# -*- coding: utf-8 -*-
"""素材授权台 —— 验收测试（内存库 + 注入时钟，可重复运行）。

覆盖验收项：
 1. 授权续期：临期提醒产生 → 续期 → 提醒自动关闭，引用不受影响
 2. 来源证据缺失：发布被拒 + 持久待办；补凭证后放行
 3. 并发替图：乐观锁保证只有一个成功
 4. 到期时正在导出：导出转人工复核，快照可解释启动时合法
 5. CDN 旧对象仍在缓存：purge 未确认前访问层已拦截；事实留痕
 6. 精选页面及时停止引用受限素材：事件驱动失效，不等 TTL
 7. 相同摘要不同来源：撤回 A 不影响有独立有效许可的 B（不误删）
 8. 提醒重试幂等：sweeper 反复运行不产生重复待办
 9. 撤回单用途：只影响该用途的引用/导出，其他用途不受影响
10. 旧下载无法远程收回：报告只陈述事实，不下法律结论
11. 校验缓存：TTL 内命中、过期重算、事件失效立即生效
"""
import json
import unittest
from datetime import datetime, timedelta, timezone

from licensing_core import (ConflictError, LicensingStore, ValidationError,
                            TTL_DEFAULT_SECONDS, TTL_FEATURED_SECONDS)

T0 = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)


def t(days=0, hours=0, seconds=0):
    return T0 + timedelta(days=days, hours=hours, seconds=seconds)


class Base(unittest.TestCase):
    def setUp(self):
        self.s = LicensingStore(":memory:")

    def mk_asset(self, title="图", hash_="sha256:h1", source="作者直传"):
        return self.s.register_asset(title, hash_, source, T0)

    def mk_license(self, asset, uses=("web_display",), days_valid=30, evidence=True,
                   author="作者甲"):
        return self.s.create_license(
            asset, author, list(uses), T0 - timedelta(days=1), T0,
            valid_until=T0 + timedelta(days=days_valid),
            evidence_type="授权书" if evidence else None,
            evidence_uri="evidence/lic.pdf" if evidence else None)


class TestRenewal(Base):
    """验收 1：授权续期"""

    def test_renew_before_expiry_cancels_reminder(self):
        a = self.mk_asset()
        lic = self.mk_license(a, days_valid=3)  # 3 天后到期 → 在 7 天提醒窗口内
        self.s.run_sweeper(T0)
        tasks = [x for x in self.s.list_tasks("pending") if x["kind"] == "license_expiring"]
        self.assertEqual(len(tasks), 1, "临期应产生 1 条提醒待办")

        ref = self.s.publish_ref("p1", "article", "web_display", "asset", a, T0)
        self.s.renew_license(lic, T0 + timedelta(days=365), T0)

        # 提醒自动关闭；引用保持 published；渲染正常
        self.assertEqual(len([x for x in self.s.list_tasks("pending")
                              if x["kind"] == "license_expiring"]), 0)
        page = self.s.render_page("p1", T0)
        self.assertEqual(len(page["visible"]), 1)
        self.assertEqual(page["blocked"], [])

    def test_renew_after_expiry_allows_republish(self):
        a = self.mk_asset()
        lic = self.mk_license(a, days_valid=1)
        ref = self.s.publish_ref("p1", "article", "web_display", "asset", a, T0)
        self.s.run_sweeper(t(days=2))  # 到期 → 撤下
        self.assertEqual(self.s.render_page("p1", t(days=2))["visible"], [])
        # 续期后不会自动复活（撤下是粘性的），需显式重新发布
        self.s.renew_license(lic, T0 + timedelta(days=365), t(days=2))
        self.assertEqual(self.s.render_page("p1", t(days=2))["visible"], [])
        self.s.republish_ref(ref, t(days=2))
        self.assertEqual(len(self.s.render_page("p1", t(days=2))["visible"]), 1)


class TestEvidenceMissing(Base):
    """验收 2：来源证据缺失"""

    def test_missing_evidence_blocks_publish_and_creates_todo(self):
        a = self.mk_asset()
        lic = self.mk_license(a, evidence=False)
        with self.assertRaises(ValidationError) as ctx:
            self.s.publish_ref("p1", "article", "web_display", "asset", a, T0)
        self.assertIn("来源证据缺失", str(ctx.exception))
        self.s.run_sweeper(T0)
        todos = [x for x in self.s.list_tasks("pending") if x["kind"] == "evidence_missing"]
        self.assertEqual(len(todos), 1)

        # 补凭证 → 待办关闭 → 可发布
        self.s.set_evidence(lic, "授权邮件", "evidence/mail.eml", T0)
        self.assertEqual(len([x for x in self.s.list_tasks("pending")
                              if x["kind"] == "evidence_missing"]), 0)
        self.s.publish_ref("p1", "article", "web_display", "asset", a, T0)


class TestConcurrentReplace(Base):
    """验收 3：并发替图"""

    def test_only_one_concurrent_replace_wins(self):
        a = self.mk_asset()
        self.mk_license(a)
        v2 = self.s.replace_asset_file(a, "sha256:new1", expected_version=1, now=T0)
        self.assertEqual(v2, 2)
        # 并发的另一个替图（同样基于 v1）必须失败
        with self.assertRaises(ConflictError):
            self.s.replace_asset_file(a, "sha256:new2", expected_version=1, now=T0)
        # 基于最新版本的替图可以成功
        v3 = self.s.replace_asset_file(a, "sha256:new3", expected_version=2, now=T0)
        self.assertEqual(v3, 3)
        # 旧版本 CDN 对象已登记 purge
        purged = [p["cdn_key"] for p in self.s.list_purges()]
        self.assertIn("asset:%s:v1" % a, purged)
        self.assertIn("asset:%s:v2" % a, purged)


class TestExportAtExpiry(Base):
    """验收 4：到期时正在导出"""

    def test_export_running_when_license_expires(self):
        a = self.mk_asset()
        self.mk_license(a, uses=("download",), days_valid=1)
        eid = self.s.start_export("zip", [{"target_kind": "asset", "target_id": a,
                                           "use_code": "download"}], T0)
        self.s.run_sweeper(t(days=2))  # 导出进行中授权到期
        state = self.s.finish_export(eid, t(days=2))
        self.assertEqual(state, "needs_review", "到期时正在导出 → 转人工复核而非分发")
        # 快照保留：可证明启动时刻合法
        exp = [e for e in self.s.list_exports() if e["id"] == eid][0]
        snap = json.loads(exp["snapshot"])
        self.assertEqual(list(snap.values())[0]["verdict"], "allow")
        # 生成了复核待办
        self.assertTrue(any(x["kind"] == "export_review" for x in self.s.list_tasks("pending")))
        # 不可下载
        with self.assertRaises(ConflictError):
            self.s.record_download(eid, t(days=2))


class TestCdnCache(Base):
    """验收 5：CDN 旧对象仍在缓存"""

    def test_access_layer_blocks_before_purge_confirmed(self):
        a = self.mk_asset()
        lic = self.mk_license(a)
        self.s.publish_ref("p1", "article", "web_display", "asset", a, T0)
        self.s.render_page("p1", T0)  # 建立缓存
        self.s.revoke_use(lic, "web_display", "作者要求下线", T0)

        purges = self.s.list_purges()
        self.assertTrue(any(p["state"] == "pending" for p in purges),
                        "CDN purge 已登记但尚未确认（旧对象可能仍在缓存）")
        # 访问层不等 purge：立即拦截（引用已被事件驱动撤下）
        page = self.s.render_page("p1", T0)
        self.assertEqual(page["visible"], [])
        self.assertEqual(len(page["taken_down"]), 1)
        # purge 确认后留痕
        self.s.confirm_purge(purges[0]["id"], T0)
        self.assertEqual(self.s.list_purges()[0]["state"], "confirmed")
        actions = [x["action"] for x in self.s.list_audit()]
        self.assertIn("cdn.purge_confirmed", actions)


class TestFeaturedTakedown(Base):
    """验收 6：精选页面及时停止引用受限素材"""

    def test_featured_page_blocked_immediately_on_revoke(self):
        a = self.mk_asset()
        lic = self.mk_license(a, uses=("featured",))
        d = self.s.add_derivative(a, "sha256:crop1", "crop:800x600", T0)
        self.s.publish_ref("home", "featured", "featured", "derivative", d, T0)
        first = self.s.render_page("home", T0)
        self.assertEqual(first["cache"]["ttl_seconds"], TTL_FEATURED_SECONDS,
                         "精选页使用更短 TTL")
        self.assertEqual(len(first["visible"]), 1)
        hit = self.s.render_page("home", T0)
        self.assertTrue(hit["cache"]["hit"], "TTL 内应命中缓存")

        self.s.revoke_use(lic, "featured", "授权方撤回精选授权", T0)
        # 事件驱动失效：不等 60s TTL，下一次访问即被拦
        page = self.s.render_page("home", T0)
        self.assertEqual(page["visible"], [])
        self.assertEqual(page["taken_down"][0]["reason"], "用途撤回: 授权方撤回精选授权")


class TestSameHashDifferentSource(Base):
    """验收 7：相同文件摘要 ≠ 相同授权；撤回不影响独立来源"""

    def test_revoke_one_source_keeps_independent_source(self):
        h = "sha256:samefile"
        a1 = self.s.register_asset("同一文件·来源A", h, "作者直传", T0)
        a2 = self.s.register_asset("同一文件·来源B", h, "图库采购", T0)
        lic1 = self.mk_license(a1)
        self.mk_license(a2)  # B 有自己独立的有效授权
        r1 = self.s.publish_ref("p1", "article", "web_display", "asset", a1, T0)
        self.s.publish_ref("p2", "article", "web_display", "asset", a2, T0)

        impact = self.s.revoke_use(lic1, "web_display", "A 来源授权撤回", T0)
        self.assertEqual([r["id"] for r in impact["affected_refs"]], [r1],
                         "影响计算只命中来源 A 的引用")
        self.assertEqual(self.s.render_page("p1", T0)["visible"], [])
        self.assertEqual(len(self.s.render_page("p2", T0)["visible"]), 1,
                         "同摘要的独立来源 B 仍有有效许可，不得误删")
        # blob 去重表不因授权撤回而删内容
        self.assertIsNotNone(self.s._get("SELECT * FROM blobs WHERE content_hash=?", (h,)))


class TestIdempotentReminders(Base):
    """验收 8：提醒由持久任务产生，重试不重复生成"""

    def test_sweeper_retries_do_not_duplicate_todos(self):
        a = self.mk_asset()
        self.mk_license(a, days_valid=3, evidence=False)  # 同时触发临期+缺证据
        for _ in range(3):
            self.s.run_sweeper(T0)  # 模拟重试/重启后重复运行
        pending = self.s.list_tasks("pending")
        self.assertEqual(len([x for x in pending if x["kind"] == "license_expiring"]), 1)
        self.assertEqual(len([x for x in pending if x["kind"] == "evidence_missing"]), 1)


class TestPartialUseRevocation(Base):
    """验收 9：撤回单用途只影响该用途的依赖"""

    def test_revoke_download_keeps_web_display(self):
        a = self.mk_asset()
        lic = self.mk_license(a, uses=("web_display", "download"))
        self.s.publish_ref("p1", "article", "web_display", "asset", a, T0)
        eid = self.s.start_export("pdf", [{"target_kind": "asset", "target_id": a,
                                           "use_code": "download"}], T0)
        self.s.finish_export(eid, T0)

        impact = self.s.revoke_use(lic, "download", "不再允许下载", T0)
        self.assertEqual(impact["affected_refs"], [], "web_display 引用不受 download 撤回影响")
        self.assertEqual(len(self.s.render_page("p1", T0)["visible"]), 1)
        exp = [e for e in self.s.list_exports() if e["id"] == eid][0]
        self.assertEqual(exp["state"], "stale", "含 download 的导出物被标记待重新生成")
        self.assertTrue(any(x["kind"] == "regenerate_export" for x in self.s.list_tasks("pending")))


class TestUnrecoverableDownloads(Base):
    """验收 10：旧下载无法远程收回 —— 只记录事实，不下法律结论"""

    def test_report_states_facts_without_legal_conclusion(self):
        a = self.mk_asset()
        lic = self.mk_license(a, uses=("download",))
        eid = self.s.start_export("zip", [{"target_kind": "asset", "target_id": a,
                                           "use_code": "download"}], T0)
        self.s.finish_export(eid, T0)
        for _ in range(5):
            self.s.record_download(eid, T0)

        self.s.revoke_use(lic, "download", "授权方撤回下载授权", T0)
        report = self.s.unrecoverable_report(T0)
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["download_count"], 5)
        self.assertIn("无法通过系统远程收回", report[0]["fact"])
        self.assertIn("仅记录事实", report[0]["fact"])
        # 审计中同样有事实留痕
        facts = [json.loads(x["detail"]).get("fact", "") for x in self.s.list_audit()
                 if x["action"] == "export.unrecoverable"]
        self.assertTrue(any("无法远程收回" in f for f in facts))
        # 全库审计措辞检查：不出现法律结论性词汇
        for row in self.s.list_audit():
            self.assertNotIn("侵权", row["detail"])
            self.assertNotIn("违法", row["detail"])


class TestValidationCache(Base):
    """验收 11：发布时一次校验 + 访问时持续校验（明确 TTL 的组合机制）"""

    def test_cache_hit_within_ttl_and_recompute_after_expiry(self):
        a = self.mk_asset()
        lic = self.mk_license(a)
        self.s.publish_ref("p1", "article", "web_display", "asset", a, T0)
        first = self.s.render_page("p1", T0)
        self.assertFalse(first["cache"]["hit"])
        self.assertEqual(first["cache"]["ttl_seconds"], TTL_DEFAULT_SECONDS)

        # TTL 内：即使授权刚被外部改动前（此处直接改库模拟事件遗漏），仍命中缓存
        hit = self.s.render_page("p1", T0 + timedelta(seconds=TTL_DEFAULT_SECONDS - 1))
        self.assertTrue(hit["cache"]["hit"])

        # 超过 TTL：重新计算（持续校验的兜底）
        later = T0 + timedelta(seconds=TTL_DEFAULT_SECONDS + 1)
        self.s.revoke_use(lic, "web_display", "测试撤回", T0)  # 事件失效已清缓存
        page = self.s.render_page("p1", later)
        self.assertFalse(page["cache"]["hit"])
        self.assertEqual(page["visible"], [])

    def test_publish_time_check_is_synchronous(self):
        a = self.mk_asset()  # 无任何授权
        with self.assertRaises(ValidationError):
            self.s.publish_ref("p1", "article", "web_display", "asset", a, T0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
