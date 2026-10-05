import json, sys
r = json.load(open(sys.argv[1], encoding='utf-8'))
print(json.dumps({k: r['harness'].get(k) for k in ('browser', 'extension_dir', 'emulate_activetab', 'activetab_scope', 'frame_click', 'trigger_mode')}, ensure_ascii=False))
for s in r['scenarios']:
    print(s['status'], s['name'], '|', s['summary'][:400])
    res = s.get('result') or {}
    if res and ('-v' in sys.argv):
        print('    toasts', res.get('toasts_by_frame', res.get('toasts')), 'fid', res.get('frame_id_sent'), 'calls', res.get('clipboard_calls'),
              'sw_warn', [w['text'][:100] for w in res.get('sw_warnings', [])],
              'pe', [w['text'][:140] for w in res.get('page_errors', [])])
    if 'traceback' in s: print(s['traceback'][-1500:])
print(r['summary'])
