# ---------------------------------------------------------------------------
# Frame-routing scenarios (added for the 2.4 audit). Imported by run.py.
# ---------------------------------------------------------------------------
import json
import time
import traceback
import uuid

from cdp import CDPError

RETRY_TOAST = "もう一度テーブルを右クリックしてからお試しください。"
RELOAD_TOAST = "ページを再読み込みしてからお試しください。"

INSTR_JS = r"""
(() => {
  // harness-only instrumentation in the content-script (isolated) world: records which clipboard path ran
  const pol = document.permissionsPolicy || document.featurePolicy;
  globalThis.__h = {calls: [], env: {secure: isSecureContext, clipboard: typeof navigator.clipboard,
    focus: document.hasFocus(), pp: pol ? pol.allowsFeature('clipboard-write') : null}};
  if (!globalThis.__hWrapped) {
    if (navigator.clipboard) {
      const orig = navigator.clipboard.writeText.bind(navigator.clipboard);
      navigator.clipboard.writeText = (t) => {
        globalThis.__h.calls.push('writeText:called focus=' + document.hasFocus());
        return orig(t).then(v => { globalThis.__h.calls.push('writeText:resolved'); return v; },
                            e => { globalThis.__h.calls.push('writeText:rejected ' + e.name + ': ' + e.message); throw e; });
      };
    }
    const oe = document.execCommand.bind(document);
    document.execCommand = function (...a) {
      const r = oe(...a); globalThis.__h.calls.push('execCommand(' + a[0] + ')=' + r); return r;
    };
    globalThis.__hWrapped = true;
  }
  return globalThis.__h.env;
})()
"""


class Frame:
    """One frame of a test page: (CDP session, CDP frame id) + the extension frameId."""

    def __init__(self, h, page, name, sid, fid, ext_frame_id=None):
        self.h, self.page, self.name, self.sid, self.fid, self.ext_frame_id = h, page, name, sid, fid, ext_frame_id

    def _ctxs(self, isolated):
        out = []
        for ctx in list(self.h.cdp.contexts.get(self.sid, {}).values()):
            aux = ctx.get("auxData") or {}
            if aux.get("frameId") != self.fid:
                continue
            if isolated:
                if aux.get("type") == "isolated" and ((self.h.ext_id and self.h.ext_id in ctx.get("origin", ""))
                                                       or ctx.get("name") == self.h.ext_name):
                    out.append(ctx)
            elif aux.get("isDefault"):
                out.append(ctx)
        return out

    def cs_contexts(self):
        return self._ctxs(True)

    def eval(self, expr, **kw):
        c = self._ctxs(False)
        if not c:
            raise CDPError(f"no main-world context for frame {self.name}")
        return self.h.cdp.evaluate(self.sid, expr, context_id=c[-1]["id"], **kw)

    def cs_eval(self, expr, **kw):
        c = self._ctxs(True)
        if not c:
            raise CDPError(f"no content-script context for frame {self.name}")
        return self.h.cdp.evaluate(self.sid, expr, context_id=max(x["id"] for x in c), **kw)

    def install_observer(self):
        from run import OBSERVER_JS
        return self.eval(OBSERVER_JS % json.dumps(self.h.toast_id))

    def toasts(self):
        try:
            return self.eval("window.__copyhiro ? window.__copyhiro.toasts : null", timeout=5) or []
        except Exception:
            return []


def discover_frames(h, page, names, tag, timeout=15):
    frames = {"top": Frame(h, page, "top", page.sid, page.target_id, 0)}

    def find():
        found = {}
        try:
            tree = h.cdp.send("Page.getFrameTree", sid=page.sid, timeout=5)["frameTree"]
        except Exception:
            return None
        local = {(ch["frame"].get("name") or ""): ch["frame"] for ch in (tree.get("childFrames") or [])}
        for n in names:
            cands = []
            for s, v in list(h.cdp.sessions.items()):  # out-of-process iframe targets
                if v["type"] == "iframe" and v["attached"] and f"f={n}&" in v["url"] and f"tag={tag}" in v["url"]:
                    cands.append(Frame(h, page, n, s, v["targetId"]))
            if n in local:
                cands.append(Frame(h, page, n, page.sid, local[n]["id"]))
            for f in cands:
                try:
                    href = f.eval("location.href", timeout=3)
                    rs = f.eval("document.readyState", timeout=3)
                except Exception:
                    continue
                if rs == "complete" and ((n == "srcdoc" and href == "about:srcdoc") or (f"f={n}&" in href)):
                    found[n] = f
                    break
        return found if len(found) == len(names) else None

    found = h.cdp.wait_for(find, timeout, 0.2, what=f"frames {names}")
    frames.update(found)
    return frames


SW_FRAME_RECORDER_JS = r"""
(() => {
  // harness-only listener (not part of the extension): records sender.frameId of a ping from a content script
  if (!globalThis.__hfInstalled) {
    globalThis.__hf = [];
    chrome.runtime.onMessage.addListener((m, sender) => {
      if (m && m.__harness === 'frameid') globalThis.__hf.push({name: m.name, tag: m.tag, frameId: sender.frameId, url: sender.url});
    });
    globalThis.__hfInstalled = true;
  }
  return true;
})()
"""


def ext_frame_ids(h, page, frames, tag, wait=8):
    """extension frameIds: (1) content script pings the SW -> sender.frameId (no extra permission);
    (2) frames without a content script (srcdoc): scripting.executeScript(allFrames) results, if host access exists."""
    h.sw_eval(SW_FRAME_RECORDER_JS)
    ids = {}
    for n, f in frames.items():
        if n == "top":
            continue
        if f.cs_contexts():
            try:
                f.cs_eval("chrome.runtime.sendMessage({__harness: 'frameid', name: %s, tag: %s}).then(() => 1, e => e.message)"
                          % (json.dumps(n), json.dumps(tag)), timeout=5)
            except Exception as e:
                ids[n] = "ping failed: " + str(e)
    time.sleep(0.3)
    rec = h.sw_eval("globalThis.__hf || []")
    for x in rec:
        if x.get("tag") == tag and x.get("name") in frames:
            ids[x["name"]] = x["frameId"]
    missing = [n for n in frames if n != "top" and not isinstance(ids.get(n), int)]
    if missing and page.tab_id is not None:
        try:
            res = h.sw_eval("chrome.scripting.executeScript({target: {tabId: %d, allFrames: true}, "
                            "func: () => ({href: location.href, name: window.name})})"
                            ".then(rs => rs.map(r => ({frameId: r.frameId, v: r.result})), e => 'error: ' + e.message)"
                            % page.tab_id)
            if isinstance(res, list):
                for r in res:
                    v = r.get("v") or {}
                    if v.get("name") in missing:
                        ids[v["name"]] = r["frameId"]
            else:
                for n in missing:
                    ids[n] = f"unknown ({res})"
        except Exception as e:
            for n in missing:
                ids[n] = f"unknown ({e})"
    for n, v in ids.items():
        if isinstance(v, int):
            frames[n].ext_frame_id = v
    return ids


def frame_right_click(h, page, frame, selector, mode="cdp"):
    from run import RIGHT_CLICK_JS
    synthetic = mode == "synthetic"
    if frame.name != "top":
        page.eval(f"document.getElementById({json.dumps(frame.name)}).scrollIntoView({{block: 'center'}})")
    r = frame.eval(RIGHT_CLICK_JS % ("true" if synthetic else "false", json.dumps(selector), "null"))
    if not r or not r.get("ok"):
        raise CDPError(f"right_click failed in {frame.name}: {r}")
    x, y = r["x"], r["y"]
    if frame.name != "top":
        off = page.eval("(() => { const f = document.getElementById(%s); const b = f.getBoundingClientRect();"
                        " return {x: b.left + f.clientLeft, y: b.top + f.clientTop}; })()" % json.dumps(frame.name))
        x, y = x + off["x"], y + off["y"]
    if not synthetic:
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            p = {"type": typ, "x": x, "y": y, "button": "right" if typ != "mouseMoved" else "none",
                 "buttons": 2 if typ == "mousePressed" else 0, "clickCount": 1 if typ != "mouseMoved" else 0}
            h.cdp.send("Input.dispatchMouseEvent", p, page.sid)
        for typ in ("keyDown", "keyUp"):
            try:
                h.cdp.send("Input.dispatchKeyEvent", {"type": typ, "key": "Escape", "code": "Escape",
                                                      "windowsVirtualKeyCode": 27}, page.sid, timeout=3)
            except Exception:
                pass
    return {"x": x, "y": y}


def act_frames(h, page, frames, target, action, selector=None, frame_id=None, wait=3.0, settle=0.7,
               instrument=False, mode="cdp", pre_clicks=()):
    from run import _short, _is_err, _is_warn
    for f in frames.values():
        try:
            f.install_observer()
        except Exception:
            pass
    sentinel = f"SENTINEL-{uuid.uuid4().hex[:8]}"
    h.set_clipboard(sentinel)
    h.activate(page)
    for (pf, psel) in pre_clicks:
        frame_right_click(h, page, frames[pf], psel, mode)
    if selector:
        frame_right_click(h, page, target, selector, mode)
    env = None
    if instrument:
        try:
            env = target.cs_eval(INSTR_JS)
        except Exception as e:
            env = "instrumentation failed: " + str(e)
    n0 = {k: len(f.toasts()) for k, f in frames.items()}
    mark = len(h.cdp.logs)
    t0 = time.time()
    fid = target.ext_frame_id if frame_id is None else frame_id
    if fid is None:
        raise CDPError(f"no extension frameId known for frame {target.name} (cannot route)")
    furl = None
    if target.name != "top":
        try:
            furl = target.eval("location.href", timeout=3)
        except Exception:
            furl = None
    res = h.trigger(page, action, fid, furl)
    end = t0 + wait
    while time.time() < end:
        if any(len(f.toasts()) > n0[k] for k, f in frames.items()):
            break
        time.sleep(0.05)
    time.sleep(settle)
    toasts = {k: [t["text"] for t in f.toasts()[n0[k]:]] for k, f in frames.items()}
    calls = None
    if instrument:
        try:
            calls = target.cs_eval("globalThis.__h ? globalThis.__h.calls : null")
        except Exception as e:
            calls = "error: " + str(e)
    raw = h.read_clipboard()
    clip = raw.replace("\r\n", "\n") if isinstance(raw, str) else raw
    logs = h.cdp.logs[mark:]
    sids = {f.sid for f in frames.values()}
    return {
        "action": action, "target_frame": target.name, "frame_id_sent": fid, "trigger": res,
        "toasts_by_frame": {k: v for k, v in toasts.items() if v},
        "clipboard": clip, "clipboard_unchanged": clip == sentinel,
        "clipboard_env": env, "clipboard_calls": calls,
        "sw_errors": [_short(e) for e in logs if e.get("target_type") == "service_worker" and _is_err(e)],
        "sw_warnings": [_short(e) for e in logs if e.get("target_type") == "service_worker" and _is_warn(e)],
        "page_errors": [_short(e) for e in logs if e.get("sid") in sids and _is_err(e)],
        "page_warnings": [_short(e) for e in logs if e.get("sid") in sids and _is_warn(e)],
        "elapsed_ms": int((time.time() - t0) * 1000),
    }


def check_frames(r, target, exp_clip, exp_toast, allow_policy_noise=True):
    probs = []
    if exp_clip is None:
        if not r["clipboard_unchanged"]:
            probs.append(f"clipboard changed unexpectedly: {r['clipboard']!r}")
    elif r["clipboard"] != exp_clip:
        probs.append("clipboard mismatch" + (" (nothing copied)" if r["clipboard_unchanged"] else f": {r['clipboard']!r}"))
    tb = r["toasts_by_frame"]
    if exp_toast is None:
        if tb:
            probs.append(f"unexpected toast(s): {tb}")
    else:
        got = tb.get(target, [])
        if got != [exp_toast]:
            probs.append(f"toast in {target}: {got} (want [{exp_toast!r}])")
        others = {k: v for k, v in tb.items() if k != target}
        if others:
            probs.append(f"toast(s) in OTHER frames: {others}")
    if r["sw_errors"]:
        probs.append("SW error: " + r["sw_errors"][0]["text"][:160])
    errs = [e for e in r["page_errors"] if not (allow_policy_noise and "ermissions policy" in (e.get("text") or ""))]
    if errs:
        probs.append("page/content-script error: " + errs[0]["text"][:200])
    if r["trigger"] and not r["trigger"].get("ok"):
        probs.append("handler rejected: " + str(r["trigger"].get("error")))
    return probs


def _t1(f):
    return f"枠\t値\n{f}\t2"


def _t1md(f):
    return f"| 枠 | 値 |\n| --- | --- |\n| {f} | 2 |"


def _all(f):
    return f"枠\t値\n{f}\t2\n\nx\ty"


def _allmd(f):
    return f"| 枠 | 値 |\n| --- | --- |\n| {f} | 2 |\n\n| x | y |\n| --- | --- |"


OUTER = "外側\t値\nouter\t1"
FRAME_NAMES = ["same", "cross", "xsec", "xsecallow", "srcdoc"]


def _open_frames_page(h, tag):
    p = h.open_page(h.secure_base + "/frames.html?" + tag)
    frames = discover_frames(h, p, FRAME_NAMES, tag)
    layout = {n: {"out_of_process": f.sid != p.sid, "content_script": bool(f.cs_contexts())} for n, f in frames.items()}
    ids = ext_frame_ids(h, p, frames, tag)
    for n, f in frames.items():
        layout[n]["ext_frame_id"] = f.ext_frame_id
    return p, frames, ids, layout


def scenario_g(c):
    """(g) real frame routing: same-origin / cross-origin (http + secure) / srcdoc iframes, fresh tab."""
    from run import _record
    h = c.h
    mode = getattr(c, "frame_click", "cdp")
    tag = "g" + uuid.uuid4().hex[:6]
    p, F, ids, layout = _open_frames_page(h, tag)
    _record(c, "g0_frame_layout", "g", "info", f"getFrameId={ids}", layout=layout)
    T = c.toast
    cases = [
        # name, target, selector, action, expected clipboard, expected toast, pre-clicks, instrument
        ("g1_same_origin_iframe_copy_table", "same", "#lab", "copy_table", _t1("same"), T("copy_table"), (), True),
        ("g2_cross_origin_http_iframe_copy_table_markdown", "cross", "#lab", "copy_table_markdown", _t1md("cross"),
         T("copy_table_markdown"), (), True),
        ("g3_cross_origin_secure_iframe_no_allow_copy_table", "xsec", "#lab", "copy_table", _t1("xsec"), T("copy_table"), (), True),
        ("g4_cross_origin_secure_iframe_allow_clipboard_write", "xsecallow", "#lab", "copy_table", _t1("xsecallow"),
         T("copy_table"), (), True),
        ("g5_same_origin_iframe_copy_all_only_frame_tables", "same", "#fout", "copy_all_tables", _all("same"),
         T("copy_all_tables", 2), (), True),
        ("g6_cross_origin_iframe_copy_all_markdown", "cross", "#fout", "copy_all_tables_markdown", _allmd("cross"),
         T("copy_all_tables_markdown", 2), (), False),
        ("g6b_cross_origin_secure_iframe_copy_all", "xsec", "#fout", "copy_all_tables", _all("xsec"),
         T("copy_all_tables", 2), (), False),
        ("g7_top_copy_table_after_iframe_rightclick", "top", "#outer td", "copy_table", OUTER, T("copy_table"),
         (("same", "#lab"),), True),
        ("g8_top_copy_all_excludes_iframe_tables", "top", "#top-outside", "copy_all_tables", OUTER,
         T("copy_all_tables", 1), (), False),
        ("g9_iframe_action_leaves_top_untouched", "same", "#lab", "copy_table", _t1("same"), T("copy_table"),
         (("top", "#outer td"),), False),
        ("g10_iframe_outside_table_not_found", "cross", "#fout", "copy_table", None, T("not_found"), (), False),
    ]
    for name, tgt, sel, action, exp_clip, exp_toast, pre, instr in cases:
        try:
            r = act_frames(h, p, F, F[tgt], action, sel, instrument=instr, mode=mode, pre_clicks=pre)
            probs = check_frames(r, tgt, exp_clip, exp_toast)
            st = "fail" if probs else "pass"
            if probs and r["toasts_by_frame"].get(tgt) == [T("failed")] and not r["sw_errors"]:
                st = "warn"  # graceful failure
            _record(c, name, "g", st, "; ".join(probs) or "ok", result=r)
        except Exception as e:
            _record(c, name, "g", "error", f"{type(e).__name__}: {e}", traceback=traceback.format_exc())

    # srcdoc iframe: about:srcdoc is NOT matched by content_scripts (<all_urls>, no match_about_blank)
    sd = F["srcdoc"]
    cs_before = layout["srcdoc"]["content_script"]
    if sd.ext_frame_id is None:
        _record(c, "g11_srcdoc", "g", "skip", "srcdoc frameId unknown without host access (no content script there to "
                "ping; scripting.executeScript denied) -- covered by the --emulate-activetab runs")
        p.close()
        return
    try:
        r1 = act_frames(h, p, F, sd, "copy_table", "#t1 td", mode=mode)
        r2 = act_frames(h, p, F, sd, "copy_table", "#t1 td", mode=mode)
        r3 = act_frames(h, p, F, sd, "copy_all_tables", "#fout", mode=mode)
        p1 = check_frames(r1, "srcdoc", _t1("srcdoc"), T("copy_table"))
        p2 = check_frames(r2, "srcdoc", _t1("srcdoc"), T("copy_table"))
        p3 = check_frames(r3, "srcdoc", _all("srcdoc"), T("copy_all_tables", 2))
        silent1 = r1["clipboard_unchanged"] and not r1["toasts_by_frame"]
        st1 = "pass" if not p1 else ("warn" if (r1["toasts_by_frame"].get("srcdoc") == [RETRY_TOAST] or silent1) else "fail")
        _record(c, "g11_srcdoc_iframe_first_copy_table", "g", st1,
                f"content script present in srcdoc frame before click={cs_before}; " +
                ("SILENT NO-OP; " if silent1 else "") + ("; ".join(p1) or "ok"), result=r1)
        _record(c, "g11b_srcdoc_iframe_retry_copy_table", "g", "fail" if p2 else "pass", "; ".join(p2) or "ok", result=r2)
        _record(c, "g11c_srcdoc_iframe_copy_all", "g", "fail" if p3 else "pass", "; ".join(p3) or "ok", result=r3)
    except Exception as e:
        _record(c, "g11_srcdoc", "g", "error", f"{type(e).__name__}: {e}", traceback=traceback.format_exc())
    p.close()


def scenario_h(c):
    """(h) frames page opened BEFORE chrome.runtime.reload(): lazy re-injection per frame."""
    from run import _record, _short, _is_err
    h = c.h
    mode = getattr(c, "frame_click", "cdp")
    tag = "h" + uuid.uuid4().hex[:6]
    p, F, ids, layout = _open_frames_page(h, tag)
    # the user right-clicked in some frames before the update (orphaned scripts keep that lastElement)
    for fname, sel in (("top", "#outer td"), ("same", "#lab"), ("cross", "#lab")):
        frame_right_click(h, p, F[fname], sel, mode)
    sw_logs = h.reload_extension()
    errs = [_short(e) for e in sw_logs if _is_err(e)]
    _record(c, "h0_reload", "h", "fail" if errs else "info",
            f"SW errors during reload: {errs}" if errs else f"reloaded; activeTab scope={getattr(c, 'activetab_scope', None)}",
            layout=layout)
    T = c.toast
    seq = [
        # name, frame, selector, action, expected-when-injection-allowed (clip, toast)
        ("h1_top_first_copy_table", "top", "#outer td", "copy_table", (None, RETRY_TOAST)),
        ("h1b_top_retry_copy_table", "top", "#outer td", "copy_table", (OUTER, T("copy_table"))),
        ("h2_same_origin_iframe_first_copy_table", "same", "#lab", "copy_table", (None, RETRY_TOAST)),
        ("h2b_same_origin_iframe_retry", "same", "#lab", "copy_table", (_t1("same"), T("copy_table"))),
        ("h3_cross_origin_http_iframe_first_copy_table", "cross", "#lab", "copy_table", (None, RETRY_TOAST)),
        ("h3b_cross_origin_http_iframe_retry", "cross", "#lab", "copy_table", (_t1("cross"), T("copy_table"))),
        ("h4_cross_origin_secure_iframe_copy_all", "xsec", "#fout", "copy_all_tables", (_all("xsec"), T("copy_all_tables", 2))),
        ("h5_srcdoc_iframe_copy_all", "srcdoc", "#fout", "copy_all_tables", (_all("srcdoc"), T("copy_all_tables", 2))),
        ("h6_top_copy_all_after_reinjection", "top", "#top-outside", "copy_all_tables", (OUTER, T("copy_all_tables", 1))),
    ]
    scope = getattr(c, "activetab_scope", None)
    for name, fname, sel, action, (exp_clip, exp_toast) in seq:
        if F[fname].ext_frame_id is None:
            _record(c, name, "h", "skip", f"frameId of {fname} unknown without host access")
            continue
        toast_frame = fname
        if scope == "top" and fname in ("cross", "xsec"):
            # activeTab covers only the top-level origin: the cross-origin frame can not be injected, so the
            # extension asks for a reload in the TOP frame (action notify_reload) and copies nothing
            exp_clip, exp_toast, toast_frame = None, RELOAD_TOAST, "top"
        try:
            r = act_frames(h, p, F, F[fname], action, sel, mode=mode)
            probs = check_frames(r, toast_frame, exp_clip, exp_toast)
            silent = r["clipboard_unchanged"] and not r["toasts_by_frame"]
            st = "fail" if probs else "pass"
            if silent and not r["sw_errors"] and not r["page_errors"]:
                st = "warn"
            _record(c, name, "h", st, ("SILENT NO-OP (injection not permitted?): " if silent else "") +
                    ("; ".join(probs) or "ok"), result=r, cs_contexts_in_frame=len(F[fname].cs_contexts()))
        except Exception as e:
            _record(c, name, "h", "error", f"{type(e).__name__}: {e}", traceback=traceback.format_exc())
    p.close()


def scenario_i(c):
    """(i) table cell that contains a custom element with an OPEN shadow root (component inside a cell)."""
    from run import _record
    h = c.h
    p = h.open_page(h.secure_base + "/shadowcell.html")
    T = c.toast
    for name, sel in (("i1_rightclick_inside_component_shadow_root_in_cell",
                       "js:document.querySelector('#badge').shadowRoot.querySelector('#inner')"),
                      ("i2_rightclick_slotted_light_dom_in_cell", "#slotted"),
                      ("i3_rightclick_component_host_itself", "#badge")):
        p.right_click(sel)
        r = h.act(p, "copy_table")
        ok = (not r["clipboard_unchanged"]) and r["clipboard"].startswith("名前\t状態") and r["toasts"] == [T("copy_table")]
        _record(c, name, "i", "pass" if ok else "fail", f"toasts={r['toasts']} clipboard={r['clipboard']!r}", result=r)
    p.close()
    q = h.open_page(h.secure_base + "/shadow.html")
    r = h.act(q, "copy_all_tables")
    _record(c, "i4_copy_all_with_table_in_open_shadow_root", "i", "info",
            f"toasts={r['toasts']} clipboard_unchanged={r['clipboard_unchanged']} "
            f"(document.querySelectorAll does not enter shadow roots)", result=r)
    q.close()


def scenario_j(c):
    """(j) menu click while the page is still loading (before document_idle): fallback injection vs declared script."""
    from run import _record, _short, _is_err, _is_warn, Page
    h = c.h
    url = h.secure_base + "/slow.html?hold=5&" + uuid.uuid4().hex[:6]
    before = {t["id"] for t in (h.tabs() or [])}
    tid = h.cdp.send("Target.createTarget", {"url": url})["targetId"]
    sid = h.cdp.wait_for(lambda: next((s for s, v in list(h.cdp.sessions.items())
                                       if v["targetId"] == tid and v["attached"]), None), 10, what="slow session")
    p = Page(h, sid, tid, url)

    def table_ready():
        try:
            return p.eval("document.getElementById('slowt') ? document.readyState : false", timeout=3)
        except Exception:
            return False
    state = h.cdp.wait_for(table_ready, 10, 0.05, what="slow table")
    p.tab_id = h.cdp.wait_for(lambda: next((t["id"] for t in (h.tabs() or []) if t["id"] not in before), None), 5)
    p.install_observer()
    cs_before = bool(p.cs_contexts())
    p.right_click("#slowt td")
    mark = len(h.cdp.logs)
    t0 = time.time()
    res = h.trigger(p, "copy_table")
    t_trig = time.time() - t0
    time.sleep(1.0)
    toasts = [t["text"] for t in p.toasts()]
    logs = h.cdp.logs[mark:]
    page_errs = [_short(e) for e in logs if e.get("sid") == sid and _is_err(e)]
    ctxs = len(p.cs_contexts())
    st = "info"
    if any("already been declared" in (e.get("text") or "") for e in page_errs):
        st = "warn"
    if len(toasts) > 1:
        st = "fail"
    _record(c, "j1_menu_click_while_loading", "j", st,
            f"readyState at click={state}; content script present at click={cs_before}; handler took {t_trig:.1f}s; "
            f"toasts={toasts}; content-script contexts now={ctxs}; page errors={[e['text'][:120] for e in page_errs]}",
            trigger=res, page_errors=page_errs,
            sw_warnings=[_short(e) for e in logs if e.get("target_type") == "service_worker" and _is_warn(e)])
    # afterwards (page fully loaded, declared content script present) exactly one live listener must answer
    h.cdp.wait_for(lambda: p.eval("document.readyState", timeout=3) == "complete", 15, 0.1, what="slow page load")
    try:
        h.cdp.wait_for(p.cs_contexts, 6, 0.1, what="content script")
    except CDPError:
        pass
    p.right_click("#slowt td")
    r = h.act(p, "copy_table")
    ok = r["clipboard"] == "遅\t値\nslow\t9" and r["toasts"] == [c.toast("copy_table")]
    _record(c, "j2_after_load_single_handler", "j", "pass" if ok else "fail",
            f"toasts={r['toasts']} clipboard={r['clipboard']!r}", result=r)
    p.close()


def scenario_k(c):
    """(k) pages with copy-event handlers (copy protection / copyright appenders) -- non-secure (execCommand path) and secure."""
    from run import _record
    h = c.h
    want = "保護\t値\nguard\t7"
    for base_name, base in (("nonsecure", h.nonsecure_base), ("secure", h.secure_base)):
        for mode in ("block", "append", "window"):
            p = h.open_page(f"{base}/copyguard.html?mode={mode}")
            p.right_click("#g td")
            r = h.act(p, "copy_table")
            if r["clipboard"] == want and r["toasts"] == [c.toast("copy_table")]:
                st, s = "pass", "copied"
            elif r["toasts"] == [c.toast("failed")] and r["clipboard_unchanged"]:
                st, s = "warn", "graceful failure toast (page swallowed the copy event)"
            else:
                st, s = "fail", f"toasts={r['toasts']} clipboard={r['clipboard']!r}"
            _record(c, f"k_{base_name}_{mode}", "k", st, s, result=r)
            p.close()
