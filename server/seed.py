# -*- coding: utf-8 -*-
"""演示数据：覆盖授权台的关键形态（同摘要不同来源、临期、缺证据、精选页、导出物）。"""
import sys
from datetime import datetime, timedelta, timezone

from licensing_core import LicensingStore

now = datetime.now(timezone.utc).replace(microsecond=0)
db = sys.argv[1] if len(sys.argv) > 1 else "server/licensing.db"
s = LicensingStore(db)

# 同一文件摘要、两个不同来源 → 两条 asset，授权互不影响
HASH_SUNSET = "sha256:9f2cSunsetLake"
a1 = s.register_asset("湖畔落日·横版", HASH_SUNSET, "作者直传-林小满", now,
                      byte_size=820_123, source_ref="mail-2026-0312")
a2 = s.register_asset("湖畔落日（图库版）", HASH_SUNSET, "图库-视界中国", now,
                      byte_size=820_123, source_ref="vcg-order-8842")

# a1：作者直传授权，7 天内到期（触发临期提醒），用途含精选/下载
lic1 = s.create_license(a1, "林小满", ["web_display", "featured", "download"],
                        now - timedelta(days=80), now,
                        valid_until=now + timedelta(days=5),
                        evidence_type="授权邮件", evidence_uri="evidence/mail-2026-0312.eml",
                        note="作者邮件授权，仅限本站")
# a2：图库授权，凭证齐全（与 a1 同摘要但授权独立）
lic2 = s.create_license(a2, "视界中国（代理）", ["web_display"], now - timedelta(days=30), now,
                        valid_until=now + timedelta(days=300), evidence_type="采购合同",
                        evidence_uri="evidence/vcg-order-8842.pdf")

# a3：来源证据缺失的授权（触发缺证据提醒；发布时校验会拒绝引用它）
a3 = s.register_asset("山间步道·晨雾", "sha256:morningTrail", "投稿-未核实用户", now,
                      byte_size=640_000, source_ref="upload-9917")
lic3 = s.create_license(a3, "未核实用户", ["web_display"], now - timedelta(days=2), now,
                        valid_until=now + timedelta(days=90), evidence_type=None)

# 派生裁图 + 正文引用 + 精选页引用
d1 = s.add_derivative(a1, "sha256:cropSunset16x9", "crop:1600x900@center", now)
s.publish_ref("articles/lakeside-walk", "article", "web_display", "asset", a1, now)
s.publish_ref("home-featured", "featured", "featured", "derivative", d1, now)
s.publish_ref("articles/lakeside-walk", "article", "web_display", "asset", a2, now)

# 一个已完成且有下载的导出物
eid = s.start_export("pdf", [{"target_kind": "asset", "target_id": a1, "use_code": "download"}], now)
s.finish_export(eid, now)
s.record_download(eid, now)
s.record_download(eid, now)

# 周期任务：生成临期/缺证据提醒（幂等）
print("sweeper:", s.run_sweeper(now))
print("seeded ->", db)
print("assets:", a1, a2, a3, "| derivative:", d1, "| export:", eid)
