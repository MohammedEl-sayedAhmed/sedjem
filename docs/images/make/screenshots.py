"""The README screenshots, taken from a throwaway Sedjem server with the made-up meeting in content.py.
Nothing is read from or written to your own data: the server runs on a temporary folder.
Run from the repository root with the project's Python (it needs the app's packages and Playwright with Chrome):
    python -P docs/images/make/screenshots.py [shot names...]
The shots are saved at twice the size in docs/images/make/out/; shrink.py turns them into the files in docs/images/.
The shot named "site" is the website's: one region of the app per claim, light and dark, from a desktop window at
twice the size and a phone's at three times, saved in out/site/ (python docs/images/make/shrink.py site then makes
the lossless files in site/captures/)."""
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
WT = HERE.parent.parent.parent
OUT = HERE / "out"
ONLY = set(sys.argv[1:])
TMP = Path(tempfile.mkdtemp(prefix="sedjem-shots-"))
os.environ["HF_HOME"] = str(TMP / "hf")
sys.path.insert(0, str(WT / "tests"))
sys.path.insert(0, str(WT))
sys.path.insert(0, str(HERE))
for k in list(os.environ):
    if k.endswith(("_API_KEY", "_SPEECH_KEY", "_SPEECH_REGION")):
        del os.environ[k]

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

import ui_support  # noqa: E402
from app import config as appconfig, engines, server as appserver  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402
import content  # noqa: E402

HOME = TMP / "home"
(HOME / "app_data").mkdir(parents=True)
# The first-start questions are answered: no update checks, and no usage counts from a throwaway copy
(HOME / "app_data" / "settings.json").write_text(json.dumps({"update_check": False}), encoding="utf-8")
os.environ["SEDJEM_NO_USAGE"] = "1"
OUT.mkdir(parents=True, exist_ok=True)

MACHINE = {"manufacturer": "Dell Inc.", "model": "XPS 15 9530", "os": "Ubuntu 24.04.3 LTS (Linux 6.14.0)",
           "cpu": "13th Gen Intel(R) Core(TM) i7-13700H", "threads": 20, "ram_gb": 31.6, "arch": "x86_64",
           "gpus": ["NVIDIA GeForce RTX 4050 Laptop GPU", "Intel(R) Iris(R) Xe Graphics"]}
GPU = {"devices": [{"name": "NVIDIA GeForce RTX 4050 Laptop GPU", "kind": "vulkan", "type": "dgpu", "memory": 6442450944},
                   {"name": "Intel(R) Iris(R) Xe Graphics (RPL-P)", "kind": "vulkan", "type": "igpu", "memory": 0}],
       "cuda_devices": 1, "cuda_libs": None}
SOURCE = {"name": "sprint-planning.m4a", "extension": "m4a", "format": "mov,mp4,m4a,3gp,3g2,mj2", "codec": "aac",
          "codec_name": "AAC (Advanced Audio Coding)", "sample_rate": 48000, "channels": 1, "bit_rate": 96000,
          "size": 2380000, "duration": 0, "video": None}

A, B, C = "20260925-100000-a1a1", "20260925-100500-b2b2", "20260925-101000-c3c3"
LIVE = "20260926-090000-d9d9"
first = content.lines()
secs = round(first[-1]["end"] + 1.5, 1)

# The Cohere run as the model wrote it: two terms in Arabic letters, fixed by hand later (version 2)
v0 = [dict(x) for x in first]
v0[2]["text"] = "The login bug is on the board already, صح يا كريم"
v0[14]["text"] = "هكلم أحمد من الباك اند النهارده وأتأكد من ال timeline"
v0[12]["text"] = "أنا شايف إنه ايت، فيه API جديدة كمان"  # left for the compare view: whisper-medium has it right
v0[20]["text"] ="ممكن نعمل كاش لل dependencies ونقسم ال tests على أكتر من runner؟"


def set_fields(jid, **kw):
    f = HOME / "app_data" / "jobs" / jid / "job.json"
    j = json.loads(f.read_text(encoding="utf-8"))
    j.update(kw)
    f.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")


def job(jid, took, **kw):
    base = dict(title=content.TITLE, lines=first, seconds=secs, machine=MACHINE, source_name="sprint-planning.m4a",
                source=dict(SOURCE, duration=secs), audio_s=secs,
                device="vulkan: NVIDIA GeForce RTX 4050 Laptop GPU")
    base.update(kw)
    ui_support.demo_job(HOME, jid, **base)
    set_fields(jid, seconds=took)


job(A, lines=v0, created="2026-09-25T10:00:00+03:00", started="2026-09-25T10:00:02+03:00",
    finished="2026-09-25T10:00:31+03:00", took=29.4, rtf=round(29.4 / secs, 3), peak_rss_mb=1240)
job(B, lines=content.second(), rerun_of=A, model="whisper-medium", model_title="whisper-medium code-switching",
    engine="whisper", model_file="whisper-medium-arabic-codeswitched-ct2", created="2026-09-25T10:05:00+03:00",
    started="2026-09-25T10:05:01+03:00", finished="2026-09-25T10:06:12+03:00", took=71.2,
    rtf=round(71.2 / secs, 3), peak_rss_mb=2050, device="cuda: NVIDIA GeForce RTX 4050 Laptop GPU")
hosted_lines = [dict(x) for x in first]
hosted_lines[5]["text"] = "طب مين هياخده ال sprint دي؟"
hosted_lines[21]["text"] = "ممكن، هجرب ال parallel jobs وأقولكم بكرة"
job(C, lines=hosted_lines, rerun_of=A, model="elevenlabs", model_title="ElevenLabs Scribe v2", kind="hosted",
    engine="elevenlabs", model_file=None, api_model="scribe_v2", device=None, threads=None, power=None,
    created="2026-09-25T10:10:00+03:00", started="2026-09-25T10:10:01+03:00",
    finished="2026-09-25T10:10:22+03:00", took=21.0, rtf=round(21.0 / secs, 3), peak_rss_mb=None)
for i, (title, jid, raw) in enumerate(content.OTHER):
    ls = content.lines(raw)
    ui_support.demo_job(HOME, jid, title=title, lines=ls, seconds=round(ls[-1]["end"] + 1, 1), machine=MACHINE,
                        created=f"2026-09-2{4 - i}T15:10:00+03:00", speaker_names={"1": "Mona", "2": "Karim", "3": "Omar"})

# Models on the computer: sparse files of the right size, so they count as downloaded and take no space
cfg0 = Config(home=HOME)
for m in cfg0.models.values():
    if m["id"] in ("whisper-medium", "cohere"):
        for f in m.get("files", []):
            p = HOME / f["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb") as fh:
                fh.truncate(f["size"])
for f in (f for m in cfg0.local.get("speaker_models", []) if m["id"] == "titanet-small" for f in m.get("files", [])):
    p = HOME / f["path"]
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as fh:
        fh.truncate(f["size"])
(HOME / "app_data" / "secrets.json").write_text(json.dumps({"elevenlabs": "demo-key", "gemini": "demo-key"}))
# a recording to pick on the new-transcription page
sf.write(HOME / "sprint-planning.wav", np.zeros(int(secs * 16000), dtype=np.int16), 16000, subtype="PCM_16")

patches = [mock.patch.object(engines, "gpu_info", return_value=GPU),
           mock.patch.object(appserver, "power_profile", return_value="performance"),
           mock.patch.object(appconfig, "power_profile", return_value="performance")]
for p in patches:
    p.start()
srv, app = make_server(Config(home=HOME), port=0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
print("server", BASE)

HIDE_PATHS = """() => {
  for (const el of document.querySelectorAll('body *')) {
    for (const n of el.childNodes) {
      if (n.nodeType === 3 && n.nodeValue.includes(%r)) n.nodeValue = n.nodeValue.split(%r).join('~/.local/share/sedjem').replace('~/.local/share/sedjem/app_data', '~/.local/share/sedjem');
    }
    if (el.value && typeof el.value === 'string' && el.value.includes(%r)) el.value = '~/.local/share/sedjem';
    if (el.title && el.title.includes(%r)) el.title = '';
  }
}""" % (str(HOME), str(HOME), str(HOME), str(HOME))


def api(page, method, path, body=None):
    return page.evaluate("""async ([method, path, body]) => {
        const r = await fetch(path, { method, headers: { "X-Sedjem": "1", "Content-Type": "application/json" },
                                      body: body === null ? undefined : JSON.stringify(body) });
        return { status: r.status, json: await r.json().catch(() => null) };
    }""", [method, path, body])


def shoot(page, name, full=False, clip=None):
    page.evaluate(HIDE_PATHS)
    page.evaluate("() => document.querySelectorAll('.toast').forEach(t => t.remove())")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=full, clip=clip)
    print("shot", name)


def want(name):
    return not ONLY or name in ONLY


errors = []
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch(channel="chrome")
    except Exception:  # no Chrome installed: Playwright's own Chromium
        browser = pw.chromium.launch()

    def new_page(dark=False, height=900, width=1440, scale=2):
        ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=scale,
                                  color_scheme="dark" if dark else "light")
        ctx.add_init_script(f"try {{ localStorage.setItem('sedjem-theme', '{'dark' if dark else 'light'}') }} catch (e) {{}}")
        page = ctx.new_page()
        page.set_default_timeout(15000)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        return page

    def go(page, route):
        page.goto(f"{BASE}/{route}")
        page.wait_for_function("() => typeof S !== 'undefined' && S.status")

    page = new_page()
    go(page, "#/new")
    # History of the Cohere transcript: names (v1), two terms fixed by hand (v2)
    api(page, "GET", f"/api/jobs/{A}")
    r = api(page, "PATCH", f"/api/jobs/{A}", {"speaker_names": content.NAMES, "message": "Names from the meeting invite"})
    assert r["status"] == 200, r
    fixed = [dict(x) for x in v0]
    for i in (2, 14, 20):
        fixed[i]["text"] = first[i]["text"]
    r = api(page, "PATCH", f"/api/jobs/{A}", {"lines": fixed, "message": "English terms checked against the recording"})
    assert r["status"] == 200, r
    for jid in (B, C):
        api(page, "PATCH", f"/api/jobs/{jid}", {"speaker_names": {content.SPK2[k] if jid == B else k: v
                                                                 for k, v in content.NAMES.items()}})
    r = api(page, "POST", "/api/combine", {"ids": [A, C], "base": A, "message": "Omar's line from ElevenLabs",
                                            "picks": [{"start": first[21]["start"], "end": first[21]["end"], "from": C}]})
    print("combine", r["status"], (r["json"] or {}).get("error"))
    page.context.close()

    def transcript(page):
        go(page, f"#/job/{A}")
        page.wait_for_selector("#transcript .line .text")
        page.wait_for_timeout(600)

    if want("transcript"):
        page = new_page(height=1060)
        transcript(page)
        shoot(page, "transcript")
        page.context.close()
    if want("transcript-dark"):
        page = new_page(dark=True, height=1060)
        transcript(page)
        shoot(page, "transcript-dark")
        page.context.close()
    if want("compare"):
        page = new_page()
        go(page, f"#/compare/{A},{B}")
        page.wait_for_selector("#cmpGrid .cmp-row")
        page.check("#cmpOnlyDiff")
        page.wait_for_timeout(300)
        page.click("#cmpGrid [data-keep='12:1']")
        page.evaluate("() => document.querySelector('.cmp-tools').scrollIntoView({block: 'start'})")
        page.evaluate("() => { const m = document.querySelector('#main'); m.scrollTop -= 16; }")
        page.wait_for_timeout(600)
        shoot(page, "compare")
        page.context.close()
    if want("history"):
        page = new_page()
        transcript(page)
        page.click("#historyBtn")
        page.wait_for_selector("#history[open] .ver")
        page.wait_for_timeout(400)
        shoot(page, "history")
        page.context.close()
    if want("history-review"):
        page = new_page()
        transcript(page)
        page.click("#editBtn")
        page.wait_for_timeout(300)
        for i, text in ((12, "أنا شايف إنه eight story points، فيه API جديدة كمان"),
                        (17, "في حاجة كمان، ال CI pipeline بقت بطيئة جداً، ال build بياخد حوالي عشرين دقيقة")):
            page.locator(f"#transcript .line[data-i='{i}'] .text").click()
            page.keyboard.press("ControlOrMeta+A")
            page.keyboard.type(text)
        page.locator("#transcript .line[data-i='3'] .text").click()
        page.wait_for_timeout(300)
        page.click("#editBtn")
        page.wait_for_selector("#review[open] .diff")
        page.fill("#reviewMsg", "Estimates and build time as said in the meeting")
        page.wait_for_timeout(400)
        shoot(page, "history-review")
        page.click("#reviewBack")
        page.click("#discardBtn")
        page.context.close()
    if want("new-transcription"):
        page = new_page()
        go(page, "#/new")
        page.click("details.path summary")
        page.fill("#pathInput", str(HOME / "sprint-planning.wav")) if page.locator("#pathInput").count() else None
        page.click("#pathBtn")
        page.wait_for_selector(".chosen .name")
        page.click(".model-card[data-model='cohere']")
        page.wait_for_timeout(500)
        shoot(page, "new-transcription")
        page.context.close()
    for tab, name in (("models", "settings-models"), ("hosted", "settings-hosted"), ("add", "settings-recommended")):
        if not want(name):
            continue
        page = new_page()
        go(page, "#/new")
        page.click("#settingsBtn")
        page.wait_for_selector("#settings[open] .set-panel")
        page.click(f"#settings [data-set-tab='{tab}']")
        page.wait_for_selector(f"#settings #tab-{tab}[aria-selected='true']")
        if tab == "add":
            page.evaluate("""() => { const e = document.querySelectorAll('#settings [data-catalog-add]')[0];
                                    e.closest('li, .cat-item, article, div').scrollIntoView({block: 'start'});
                                    let p = e.parentElement;
                                    while (p && p.scrollHeight <= p.clientHeight + 1) p = p.parentElement;
                                    if (p) p.scrollTop -= 28; }""")
        if tab == "hosted":
            page.locator("#key-deepgram [data-key-toggle]").click()
        page.wait_for_timeout(700)
        shoot(page, name)
        page.context.close()
    if want("live-progress"):
        rest = content.lines()
        ui_support.demo_job(HOME, LIVE, title="Sprint planning, mobile team", lines=[], seconds=60.0, machine=MACHINE,
                            model="whisper-medium", model_title="whisper-medium code-switching", engine="whisper",
                            created="2026-09-26T09:00:00+03:00", status="running", stage="transcribing",
                            done=52, total=128, elapsed=412.0, audio_s=3120.0, finished=None,
                            rtf=None, device="cuda: NVIDIA GeForce RTX 4050 Laptop GPU")
        folder = HOME / "app_data" / "jobs" / LIVE
        (folder / "transcript.json").unlink()
        j = json.loads((folder / "job.json").read_text(encoding="utf-8"))
        j["seconds"] = None
        sf.write(folder / "audio.flac", np.zeros(3120 * 16000, dtype=np.int16), 16000, subtype="PCM_16")
        (folder / "job.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
        (folder / "engine").mkdir()
        (folder / "engine" / "audio.json").write_text(json.dumps(
            [dict(x, speaker=int(x["speaker"])) for x in rest[:14]], ensure_ascii=False), encoding="utf-8")
        page = new_page()
        go(page, f"#/job/{LIVE}")
        page.wait_for_selector("#transcript .line .text")
        page.wait_for_timeout(1200)
        shoot(page, "live-progress")
        page.context.close()
    if want("site"):
        # The website's captures: each the region of the app that proves one claim, at the app's own size, which is
        # the size the page shows it at and never smaller. So each is cut from a window narrow enough for the region
        # to fit: no wider than WIDE css px from a desktop's window, at twice the pixels, to sit whole in the page's
        # evidence column, and no wider than NARROW from a phone's, at three times, to sit whole on a 320 px phone.
        # Saved as they are, in PNG, in out/site/; shrink.py site makes the lossless files in site/captures/.
        SITE = OUT / "site"
        SITE.mkdir(parents=True, exist_ok=True)
        WIDE, NARROW = 656, 280
        BOX = """([sels, pad]) => { let l = 1e9, t = 1e9, r = -1e9, b = -1e9;
          for (const s of sels) { const e = document.querySelector(s); if (!e) throw new Error('no ' + s);
            const x = e.getBoundingClientRect(); l = Math.min(l, x.left); t = Math.min(t, x.top); r = Math.max(r, x.right); b = Math.max(b, x.bottom); }
          return {x: Math.max(0, l - pad), y: Math.max(0, t - pad), width: r - l + 2 * pad, height: b - t + 2 * pad}; }"""

        def save(page, name, box, wide):
            limit = WIDE if wide else NARROW
            if box["width"] > limit:
                raise SystemExit(f"site {name}: {box['width']:.0f} px wide, more than {limit}")
            page.evaluate(HIDE_PATHS)
            page.evaluate("() => document.querySelectorAll('.toast').forEach(t => t.remove())")
            page.wait_for_timeout(300)
            page.screenshot(path=str(SITE / f"{name}.png"), clip=box)
            print("site", name, round(box["width"]), "x", round(box["height"]))

        def cut(page, name, sels, wide, pad=8, show=None):
            page.evaluate("(s) => document.querySelector(s).scrollIntoView({block: 'center'})", show or sels[0])
            page.wait_for_timeout(300)
            save(page, name, page.evaluate(BOX, [sels, pad]), wide)

        def speakers(page, name, wide):
            transcript(page)
            if wide:  # the lines and the Speakers panel beside them, from the panel's top to its foot
                box = page.evaluate("""() => { const t = document.querySelector('.tools').getBoundingClientRect(),
                    p = document.querySelector('.side-col .panel').getBoundingClientRect();
                  return {x: t.left - 8, y: p.top - 8, width: p.right - t.left + 16, height: p.height + 16}; }""")
                save(page, name, box, wide)
            else:  # inside the panel's own edge, so the page's frame is the picture's one border
                cut(page, name, [".side-col .panel"], wide, pad=-4)

        def reading(page, name, wide):
            transcript(page)
            page.evaluate("() => document.querySelector('#transcript .line[data-i=\"1\"]').classList.add('current')")
            # the search and three lines, the second being heard; on a phone the three lines
            cut(page, name, ([".tools"] if wide else []) + [f"#transcript .line[data-i='{i}']" for i in range(3)], wide,
                pad=8 if wide else 6, show=".tools" if wide else "#transcript .line[data-i='1']")

        def review(page, name, wide):
            transcript(page)
            page.click("#editBtn")
            page.wait_for_timeout(300)
            page.locator("#transcript .line[data-i='12'] .text").click()
            page.keyboard.press("ControlOrMeta+A")
            page.keyboard.type("أنا شايف إنه eight story points، فيه API جديدة كمان")
            page.locator("#transcript .line[data-i='3'] .text").click()
            page.wait_for_timeout(300)
            page.click("#editBtn")
            page.wait_for_selector("#review[open] .diff")
            page.wait_for_timeout(400)
            cut(page, name, ["#review .diff"], wide, pad=-1)

        def comparing(page, name, wide):
            go(page, f"#/compare/{A},{B}")
            page.wait_for_selector("#cmpGrid .cmp-row")
            page.check("#cmpOnlyDiff")
            page.wait_for_timeout(300)
            page.click("#cmpGrid [data-keep='12:1']")
            page.evaluate("() => { document.querySelector('[data-keep=\"12:1\"]').closest('.cmp-row').id = 'shotRow'; }")
            cut(page, name, ["#shotRow"], wide, pad=0)

        def local(page, name, wide):
            # Settings, Models: the models on the computer with their sizes and states, and on a desktop the speaker
            # model in use under them, as the app shows them
            go(page, "#/new")
            page.click("#settingsBtn")
            page.wait_for_selector("#settings[open] .set-panel")
            page.click("#settings [data-set-tab='models']")
            page.wait_for_selector("#settings .spk-chosen")
            page.wait_for_timeout(500)
            if wide:  # down to the end of the speaker model's row
                box = page.evaluate(BOX, [["#settings .set-panel h3", "#settings .set-items", "#settings .spk-chosen"], 12])
                box["height"] = page.evaluate("() => document.querySelector('#settings .spk-chosen').getBoundingClientRect().bottom") + 1 - box["y"]
                save(page, name, box, wide)
            else:  # the first three models
                cut(page, name, ["#settings .set-panel h3", "#settings .set-items .set-item:nth-child(3)"], wide, pad=4)

        # (name, how, desktop window width, phone window width): each window is as narrow as the app allows for
        # the region to fit, so the region keeps the app's own size
        for shot, take, width, phone in (("speakers", speakers, 1000, 312), ("reading", reading, 1270, 312),
                                         ("review", review, 734, 358), ("compare", comparing, 690, 312),
                                         ("local", local, 940, 340)):
            for dark in (False, True):
                for wide in (True, False):
                    page = new_page(dark=dark, height=1000 if shot == "local" else 900, width=width if wide else phone,
                                    scale=2 if wide else 3)
                    take(page, f"{shot}-{'wide' if wide else 'narrow'}-{'dark' if dark else 'light'}", wide)
                    page.context.close()
    browser.close()

print("errors:", errors)
srv.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
