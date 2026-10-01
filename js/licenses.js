/* 素材授权台前端逻辑 */
(function () {
    'use strict';

    var USES = [
        ['web_display', '网页展示'], ['featured', '精选展示'], ['download', '下载'],
        ['print', '打印'], ['derivative', '二次创作'], ['commercial', '商用']
    ];
    var USE_LABEL = {};
    USES.forEach(function (u) { USE_LABEL[u[0]] = u[1]; });

    function apiBase() {
        return (localStorage.getItem('licApi') || 'http://localhost:8081').replace(/\/$/, '');
    }
    window.saveApiBase = function () {
        localStorage.setItem('licApi', document.getElementById('apiBase').value.trim());
        toast('API 地址已保存', true); loadAll();
    };

    function toast(msg, ok) {
        var t = document.getElementById('licToast');
        t.textContent = msg; t.className = ok ? 'ok' : 'err'; t.style.display = 'block';
        setTimeout(function () { t.style.display = 'none'; }, 3200);
    }

    function api(method, path, body) {
        return fetch(apiBase() + path, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined
        }).then(function (r) {
            return r.json().then(function (j) {
                if (!r.ok) throw new Error(j.message || j.error || ('HTTP ' + r.status));
                return j;
            });
        });
    }
    var GET = function (p) { return api('GET', p); };
    var POST = function (p, b) { return api('POST', p, b || {}); };

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function short(id) { return id ? esc(id.slice(0, 18)) + '…' : ''; }
    function useTag(u, revoked) {
        return '<span class="tag ' + (revoked ? 'bad' : 'info') + '">' + esc(USE_LABEL[u] || u) + (revoked ? '·已撤回' : '') + '</span>';
    }
    function stateTag(s) {
        var map = { published: ['ok', '已发布'], taken_down: ['bad', '已撤下'], active: ['ok', '有效'],
            expired: ['bad', '已到期'], running: ['info', '进行中'], done: ['ok', '已完成'],
            stale: ['warn', '待重新生成'], needs_review: ['warn', '待人工复核'], regenerated: ['mute', '已重建'],
            pending: ['warn', '待处理'], cancelled: ['mute', '已关闭'], confirmed: ['ok', '已确认'],
            withdrawn: ['bad', '已下线'] };
        var m = map[s] || ['mute', s];
        return '<span class="tag ' + m[0] + '">' + m[1] + '</span>';
    }

    /* ---------------- 概览 ---------------- */
    function loadOverview() {
        GET('/api/overview').then(function (o) {
            var cards = [
                ['素材', o.assets], ['有效授权', o.licenses_active], ['已到期授权', o.licenses_expired],
                ['已发布引用', o.refs_published], ['已撤下引用', o.refs_taken_down],
                ['待办提醒', o.tasks_pending], ['待确认CDN刷新', o.purges_pending], ['需关注导出', o.exports_attention]
            ];
            document.getElementById('ovCards').innerHTML = cards.map(function (c) {
                return '<div class="lic-card"><div class="num">' + c[1] + '</div><div class="lbl">' + c[0] + '</div></div>';
            }).join('');
        }).catch(function (e) { toast('无法连接 API：' + e.message, false); });
        GET('/api/reports/unrecoverable').then(function (list) {
            var el = document.getElementById('unrecoverable');
            if (!list.length) { el.innerHTML = '<p class="lic-sub">暂无。</p>'; return; }
            el.innerHTML = '<table class="lic-table"><tr><th>导出物</th><th>类型</th><th>状态</th><th>已下载次数</th><th>事实说明</th></tr>' +
                list.map(function (r) {
                    return '<tr><td class="mono">' + short(r.export_id) + '</td><td>' + esc(r.kind) + '</td><td>' +
                        stateTag(r.state) + '</td><td>' + r.download_count + '</td><td>' + esc(r.fact) + '</td></tr>';
                }).join('') + '</table>';
        });
    }

    /* ---------------- 素材与授权 ---------------- */
    window.createAsset = function () {
        POST('/api/assets', {
            title: v('naTitle'), content_hash: v('naHash'), source_name: v('naSource'), source_ref: v('naRef')
        }).then(function () { toast('素材已登记', true); loadAssets(); })
          .catch(function (e) { toast(e.message, false); });
    };

    function loadAssets() {
        GET('/api/assets').then(function (assets) {
            var html = assets.map(function (a) {
                var lics = a.licenses.map(function (l) {
                    var uses = l.uses.map(function (u) {
                        var s = useTag(u.use_code, !!u.revoked_at);
                        if (!u.revoked_at && l.status === 'active') {
                            s += ' <button class="btn-s danger" onclick="revokeUse(\'' + l.id + '\',\'' + u.use_code + '\')">撤回</button>';
                        }
                        return s;
                    }).join(' ');
                    return '<details class="lic-lic"><summary>' + stateTag(l.status) +
                        ' <b>' + esc(l.author) + '</b> · 区间 ' + esc(l.valid_from.slice(0, 10)) + ' ~ ' +
                        (l.valid_until ? esc(l.valid_until.slice(0, 10)) : '长期') +
                        (l.evidence_uri ? ' · <span class="tag ok">凭证齐全</span>' : ' · <span class="tag bad">来源证据缺失</span>') +
                        '</summary>' +
                        '<div style="margin-top:8px">用途：' + uses + '</div>' +
                        '<div class="lic-sub" style="margin:6px 0">凭证：' + esc(l.evidence_type || '—') + ' ' +
                        (l.evidence_uri ? '<span class="mono">' + esc(l.evidence_uri) + '</span>' : '（未提交）') +
                        ' · <span class="mono">' + esc(l.id) + '</span></div>' +
                        '<button class="btn-s" onclick="renewLicense(\'' + l.id + '\')">续期</button>' +
                        '<button class="btn-s" onclick="fixEvidence(\'' + l.id + '\')">补凭证</button>' +
                        '<button class="btn-s" onclick="previewImpact(\'' + l.id + '\')">影响预演</button>' +
                        '</details>';
                }).join('');
                var derivs = (a.derivatives || []).map(function (d) {
                    return '<span class="tag mute" title="' + esc(d.transform || '') + '">裁图 ' + short(d.id) + '</span>';
                }).join(' ');
                return '<table class="lic-table"><tr><th style="width:26%">素材</th><th>授权记录（挂在来源身份上，与文件摘要去重分离）</th></tr>' +
                    '<tr><td><b>' + esc(a.title) + '</b> ' + stateTag(a.status) +
                    '<div class="mono">' + esc(a.id) + '</div>' +
                    '<div class="lic-sub">摘要 <span class="mono">' + esc(a.content_hash) + '</span><br>来源：' +
                    esc(a.source_name) + ' ' + esc(a.source_ref || '') + '<br>版本 v' + a.version + '</div>' +
                    '<div style="margin-top:6px">' + derivs + '</div>' +
                    '<button class="btn-s" onclick="replaceFile(\'' + a.id + '\',' + a.version + ')">替图</button> ' +
                    '<button class="btn-s primary" onclick="showLicenseForm(\'' + a.id + '\')">＋新增授权</button></td>' +
                    '<td>' + (lics || '<span class="lic-sub">暂无授权记录</span>') +
                    '<div id="lf-' + a.id + '"></div></td></tr></table>';
            }).join('');
            document.getElementById('assetList').innerHTML = html || '<p class="lic-sub">暂无素材</p>';
        });
    }

    window.showLicenseForm = function (assetId) {
        var el = document.getElementById('lf-' + assetId);
        var checks = USES.map(function (u) {
            return '<label><input type="checkbox" class="lf-use" value="' + u[0] + '"> ' + u[1] + '</label>';
        }).join('');
        el.innerHTML = '<div class="lic-form" style="margin-top:10px"><h3>新增授权（编辑页）</h3>' +
            '<div class="row"><div><label>作者 *</label><input type="text" class="lf-author" placeholder="张三"></div>' +
            '<div><label>凭证类型</label><select class="lf-evtype"><option value="">（无）</option><option>授权书</option><option>授权邮件</option><option>采购合同</option><option>其他</option></select></div>' +
            '<div><label>凭证链接/位置</label><input type="text" class="lf-evuri" placeholder="evidence/xxx.pdf（留空=证据缺失）</div></div>' +
            '<div class="row"><div><label>授权区间起 *</label><input type="datetime-local" class="lf-from"></div>' +
            '<div><label>授权区间止（留空=长期）</label><input type="datetime-local" class="lf-until"></div></div>' +
            '<div class="row checks"><label style="width:100%">允许用途 *</label>' + checks + '</div>' +
            '<button class="btn-s primary" onclick="submitLicense(\'' + assetId + '\')">保存授权</button></div>';
    };

    window.submitLicense = function (assetId) {
        var root = document.getElementById('lf-' + assetId);
        var uses = Array.prototype.map.call(root.querySelectorAll('.lf-use:checked'), function (c) { return c.value; });
        var body = {
            asset_id: assetId,
            author: root.querySelector('.lf-author').value.trim(),
            evidence_type: root.querySelector('.lf-evtype').value || null,
            evidence_uri: root.querySelector('.lf-evuri').value.trim() || null,
            uses: uses,
            valid_from: toIso(root.querySelector('.lf-from').value),
            valid_until: toIso(root.querySelector('.lf-until').value)
        };
        if (!body.author || !uses.length || !body.valid_from) { toast('作者、用途、区间起点为必填', false); return; }
        POST('/api/licenses', body).then(function () { toast('授权已保存', true); loadAssets(); loadTasks(); })
            .catch(function (e) { toast(e.message, false); });
    };

    window.renewLicense = function (lid) {
        var d = prompt('续期至（格式 2027-10-01T00:00:00+00:00）：');
        if (!d) return;
        POST('/api/licenses/' + lid + '/renew', { valid_until: d })
            .then(function () { toast('已续期，临期提醒自动关闭', true); loadAssets(); loadTasks(); })
            .catch(function (e) { toast(e.message, false); });
    };

    window.fixEvidence = function (lid) {
        var uri = prompt('凭证链接/位置（如 evidence/mail.eml）：');
        if (!uri) return;
        POST('/api/licenses/' + lid + '/evidence', { evidence_type: '补充凭证', evidence_uri: uri })
            .then(function () { toast('凭证已补充', true); loadAssets(); loadTasks(); })
            .catch(function (e) { toast(e.message, false); });
    };

    window.revokeUse = function (lid, use) {
        var reason = prompt('撤回「' + (USE_LABEL[use] || use) + '」的原因：');
        if (reason == null) return;
        POST('/api/licenses/' + lid + '/revoke_use', { use_code: use, reason: reason })
            .then(function (imp) {
                var n = (imp.affected_refs || []).length;
                toast(imp.covered_elsewhere ? '该素材仍有其他有效授权，引用不受影响'
                    : ('已撤回；撤下引用 ' + ((imp.applied && imp.applied.taken_down) || 0) + ' 处'), true);
                loadAll();
            }).catch(function (e) { toast(e.message, false); });
    };

    window.previewImpact = function (lid) {
        var use = prompt('预演撤回哪个用途？(' + USES.map(function (u) { return u[0]; }).join('/') + ')');
        if (!use) return;
        GET('/api/licenses/' + lid + '/impact?use_code=' + use).then(function (imp) {
            alert(imp.covered_elsewhere ? imp.note
                : ('若撤回将影响：引用 ' + imp.affected_refs.length + ' 处，导出物 ' + imp.affected_exports.length + ' 个'));
        }).catch(function (e) { toast(e.message, false); });
    };

    window.replaceFile = function (assetId, version) {
        var h = prompt('新文件摘要（当前版本 v' + version + '，并发替图只有基于最新版本者成功）：');
        if (!h) return;
        POST('/api/assets/' + assetId + '/replace', { content_hash: h, expected_version: version })
            .then(function (r) { toast('替图成功，新版本 v' + r.version, true); loadAssets(); })
            .catch(function (e) { toast('替图冲突：' + e.message, false); });
    };

    /* ---------------- 依赖关系 ---------------- */
    window.publishRef = function () {
        POST('/api/refs', {
            page_slug: v('nrPage'), page_kind: v('nrKind'), use_code: v('nrUse'),
            target_kind: v('nrTK'), target_id: v('nrTid')
        }).then(function () { toast('发布时校验通过，已发布', true); loadDeps(); })
          .catch(function (e) { toast('发布被拒：' + e.message, false); });
    };
    window.createDerivative = function () {
        POST('/api/derivatives', { parent_asset_id: v('ndParent'), content_hash: v('ndHash'), transform: v('ndTransform') })
            .then(function () { toast('裁图已登记', true); loadDeps(); loadAssets(); })
            .catch(function (e) { toast(e.message, false); });
    };
    window.republishRef = function (rid) {
        POST('/api/refs/' + rid + '/republish')
            .then(function () { toast('已重新发布', true); loadDeps(); })
            .catch(function (e) { toast('重新发布被拒：' + e.message, false); });
    };
    function loadDeps() {
        GET('/api/refs').then(function (refs) {
            document.getElementById('refList').innerHTML = refs.length ?
                '<table class="lic-table"><tr><th>页面</th><th>类型</th><th>用途</th><th>目标</th><th>状态</th><th>操作</th></tr>' +
                refs.map(function (r) {
                    return '<tr><td class="mono">' + esc(r.page_slug) + '</td><td>' +
                        (r.page_kind === 'featured' ? '<span class="tag warn">精选页</span>' : '<span class="tag mute">普通页</span>') +
                        '</td><td>' + useTag(r.use_code) + '</td><td class="mono">' + esc(r.target_kind) + '/' + short(r.target_id) +
                        '</td><td>' + stateTag(r.state) + (r.takedown_reason ? '<div class="lic-sub">' + esc(r.takedown_reason) + '</div>' : '') +
                        '</td><td>' + (r.state === 'taken_down' ? '<button class="btn-s primary" onclick="republishRef(\'' + r.id + '\')">重新发布</button>' : '') +
                        '</td></tr>';
                }).join('') + '</table>' : '<p class="lic-sub">暂无引用</p>';
        });
        GET('/api/assets').then(function (assets) {
            var rows = [];
            assets.forEach(function (a) {
                (a.derivatives || []).forEach(function (d) {
                    rows.push('<tr><td class="mono">' + esc(d.id) + '</td><td class="mono">' + esc(d.parent_asset_id) +
                        '</td><td>' + esc(d.transform || '') + '</td><td class="lic-sub">授权链向上追溯到原素材</td></tr>');
                });
            });
            document.getElementById('derivList').innerHTML = rows.length ?
                '<table class="lic-table"><tr><th>裁图</th><th>原素材</th><th>变换</th><th>授权链</th></tr>' + rows.join('') + '</table>'
                : '<p class="lic-sub">暂无裁图</p>';
        });
    }

    /* ---------------- 校验与缓存 ---------------- */
    window.renderPage = function () {
        GET('/api/pages/' + encodeURIComponent(v('rvPage')) + '/render').then(function (p) {
            function rows(list, withReason) {
                return list.map(function (i) {
                    return '<tr><td class="mono">' + esc(i.target) + '</td><td>' + useTag(i.use_code) + '</td><td>' +
                        esc(i.reason || i.license_id || '') + '</td></tr>';
                }).join('');
            }
            document.getElementById('renderOut').innerHTML =
                '<p>' + (p.cache.hit ? '<span class="tag info">缓存命中</span>' : '<span class="tag mute">重新计算</span>') +
                ' TTL ' + p.cache.ttl_seconds + 's，有效期至 ' + esc(p.cache.expires_at) + '</p>' +
                '<h4>可见（' + p.visible.length + '）</h4><table class="lic-table">' + rows(p.visible) + '</table>' +
                '<h4>拦截（' + p.blocked.length + '）</h4><table class="lic-table">' + rows(p.blocked, true) + '</table>' +
                '<h4>已撤下（' + p.taken_down.length + '）</h4><table class="lic-table">' + rows(p.taken_down, true) + '</table>';
            loadCache();
        }).catch(function (e) { toast(e.message, false); });
    };
    window.runSweeper = function () {
        POST('/api/sweeper/run').then(function (r) {
            toast('sweeper 完成：到期 ' + r.expired + '，新建提醒 ' + r.reminders.created + '，关闭 ' + r.reminders.cancelled, true);
            loadAll();
        }).catch(function (e) { toast(e.message, false); });
    };
    function loadCache() {
        GET('/api/cache').then(function (list) {
            document.getElementById('cacheList').innerHTML = list.length ?
                '<table class="lic-table"><tr><th>缓存键</th><th>计算时间</th><th>TTL(s)</th><th>过期时间</th><th>状态</th></tr>' +
                list.map(function (c) {
                    return '<tr><td class="mono">' + esc(c.cache_key) + '</td><td>' + esc(c.computed_at) + '</td><td>' +
                        c.ttl_seconds + '</td><td>' + esc(c.expires_at) + '</td><td>' +
                        (c.expired ? '<span class="tag bad">已过期</span>' : '<span class="tag ok">有效</span>') + '</td></tr>';
                }).join('') + '</table>' : '<p class="lic-sub">暂无缓存</p>';
        });
    }

    /* ---------------- 导出与 CDN ---------------- */
    window.startExport = function () {
        POST('/api/exports', {
            kind: v('neKind'),
            items: [{ target_kind: v('neTK'), target_id: v('neTid'), use_code: v('neUse') }]
        }).then(function (r) { toast('导出已启动 ' + r.id, true); loadExports(); })
          .catch(function (e) { toast('导出启动被拒：' + e.message, false); });
    };
    window.finishExport = function (id) {
        POST('/api/exports/' + id + '/finish').then(function (r) {
            toast('导出完成，状态：' + r.state, r.state === 'done'); loadExports(); loadTasks();
        }).catch(function (e) { toast(e.message, false); });
    };
    window.downloadExport = function (id) {
        POST('/api/exports/' + id + '/download').then(function () { toast('已记录一次下载', true); loadExports(); })
            .catch(function (e) { toast(e.message, false); });
    };
    window.confirmPurge = function (id) {
        POST('/api/cdn/purges/' + id + '/confirm').then(function () { toast('purge 已确认', true); loadExports(); })
            .catch(function (e) { toast(e.message, false); });
    };
    function loadExports() {
        GET('/api/exports').then(function (list) {
            document.getElementById('exportList').innerHTML = list.length ?
                '<table class="lic-table"><tr><th>导出物</th><th>类型</th><th>状态</th><th>下载次数</th><th>启动</th><th>操作</th></tr>' +
                list.map(function (e) {
                    var ops = '';
                    if (e.state === 'running') ops += '<button class="btn-s primary" onclick="finishExport(\'' + e.id + '\')">完成导出</button>';
                    if (e.state === 'done') ops += '<button class="btn-s" onclick="downloadExport(\'' + e.id + '\')">模拟下载</button>';
                    return '<tr><td class="mono">' + short(e.id) + '</td><td>' + esc(e.kind) + '</td><td>' + stateTag(e.state) +
                        '</td><td>' + e.download_count + '</td><td class="lic-sub">' + esc(e.started_at) + '</td><td>' + ops + '</td></tr>';
                }).join('') + '</table>' : '<p class="lic-sub">暂无导出物</p>';
        });
        GET('/api/cdn/purges').then(function (list) {
            document.getElementById('purgeList').innerHTML = list.length ?
                '<table class="lic-table"><tr><th>CDN 对象键</th><th>原因</th><th>状态</th><th>请求时间</th><th>操作</th></tr>' +
                list.map(function (p) {
                    return '<tr><td class="mono">' + esc(p.cdn_key) + '</td><td>' + esc(p.reason || '') + '</td><td>' +
                        stateTag(p.state) + '</td><td class="lic-sub">' + esc(p.requested_at) + '</td><td>' +
                        (p.state === 'pending' ? '<button class="btn-s" onclick="confirmPurge(\'' + p.id + '\')">确认刷新</button>' : esc(p.confirmed_at || '')) +
                        '</td></tr>';
                }).join('') + '</table>' : '<p class="lic-sub">暂无刷新记录</p>';
        });
    }

    /* ---------------- 待办 ---------------- */
    window.executeTask = function (id) {
        POST('/api/tasks/' + id + '/execute').then(function () { toast('任务已处理', true); loadTasks(); loadExports(); })
            .catch(function (e) { toast(e.message, false); });
    };
    function loadTasks() {
        GET('/api/tasks').then(function (list) {
            var label = { license_expiring: '授权临期', evidence_missing: '来源证据缺失',
                regenerate_export: '重新生成导出物', export_review: '导出复核' };
            document.getElementById('taskList').innerHTML = list.length ?
                '<table class="lic-table"><tr><th>类型</th><th>说明</th><th>幂等键</th><th>状态</th><th>重试次数</th><th>操作</th></tr>' +
                list.map(function (t) {
                    var p = JSON.parse(t.payload);
                    return '<tr><td>' + (label[t.kind] || t.kind) + '</td><td>' + esc(p.fact || p.cause || '') +
                        '</td><td class="mono">' + esc(t.dedup_key) + '</td><td>' + stateTag(t.state) + '</td><td>' + t.attempts +
                        '</td><td>' + (t.state === 'pending' ? '<button class="btn-s primary" onclick="executeTask(\'' + t.id + '\')">处理</button>' : '') +
                        '</td></tr>';
                }).join('') + '</table>' : '<p class="lic-sub">暂无待办</p>';
        });
    }

    /* ---------------- 审计 ---------------- */
    function loadAudit() {
        GET('/api/audit').then(function (list) {
            document.getElementById('auditList').innerHTML =
                '<table class="lic-table"><tr><th>时间</th><th>操作者</th><th>动作</th><th>对象</th><th>事实详情</th></tr>' +
                list.map(function (a) {
                    return '<tr><td class="lic-sub">' + esc(a.at) + '</td><td>' + esc(a.actor) + '</td><td><span class="tag info">' +
                        esc(a.action) + '</span></td><td class="mono">' + short(a.subject) + '</td><td class="lic-sub">' +
                        esc(a.detail) + '</td></tr>';
                }).join('') + '</table>';
        });
    }

    /* ---------------- 框架 ---------------- */
    function v(id) { return document.getElementById(id).value.trim(); }
    function toIso(local) { return local ? new Date(local).toISOString() : null; }

    function loadAll() { loadOverview(); loadAssets(); loadDeps(); loadCache(); loadExports(); loadTasks(); loadAudit(); }

    document.addEventListener('DOMContentLoaded', function () {
        document.getElementById('apiBase').value = apiBase();
        // 用途下拉
        ['nrUse', 'neUse'].forEach(function (id) {
            document.getElementById(id).innerHTML = USES.map(function (u) {
                return '<option value="' + u[0] + '">' + u[1] + '</option>';
            }).join('');
        });
        // 页签切换
        document.getElementById('licTabs').addEventListener('click', function (e) {
            if (e.target.tagName !== 'BUTTON') return;
            document.querySelectorAll('#licTabs button').forEach(function (b) { b.classList.remove('active'); });
            document.querySelectorAll('.lic-panel').forEach(function (p) { p.classList.remove('active'); });
            e.target.classList.add('active');
            document.getElementById('tab-' + e.target.dataset.tab).classList.add('active');
        });
        // 健康检查
        GET('/api/health').then(function () {
            document.getElementById('apiState').textContent = '✅ 已连接';
        }).catch(function () {
            document.getElementById('apiState').textContent = '⚠️ 未连接，请先启动：python3 server/licensing_server.py';
        });
        loadAll();
    });
})();
