"""Tests for the MCP server (app/mcp.py), on demo data folders made here; nothing touches your own data or the
network.

  Protocol     both eras (the initialize handshake, and 2026-07-28 with its per-request _meta), every error
               case, batches and notifications; over real pipes: one JSON message per line and nothing else on
               stdout even when something prints, answers sent at once, and an exit when stdin closes
  SDK          the official MCP SDK (requirements-dev.txt) as the client, in both eras: tools, resources,
               prompts and completions, with its strict checks of every answer
  Data         what assistants get: the switches and hiding (a hidden transcription is as if it didn't exist),
               the log, Arabic spellings in search, reading in pieces, the lecture chosen when a video was
               transcribed twice, links to a moment in a video, and ids and addresses that try to leave the
               data folder

Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import __version__, assistants, courses  # noqa: E402
from app import mcp as sedjem_mcp  # noqa: E402
from app.config import Config  # noqa: E402

try:
    import anyio
    from mcp import types as sdk_types
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.shared.exceptions import MCPError
except ImportError:  # the SDK is only for these tests: pip install -r requirements-dev.txt
    ClientSession = None

MEETING, OTHER_MEETING = "20261019-100000-aaaa", "20261018-150000-abcd"
INTRO, TOKENS_OLD, TOKENS_NEW, EMBED, CONTEXT = ("20261017-090000-b001", "20261017-090000-b002", "20261017-120000-b022",
                                                 "20261017-090000-b003", "20261017-090000-b004")
TALK = "20261016-080000-cccc"
COURSE = {"id": "PLprompt01", "title": "Prompt Engineering", "url": "https://www.youtube.com/playlist?list=PLprompt01",
          "count": 5, "folder": "Prompt Engineering"}
MEETING_LINES = [
    {"start": 0.0, "end": 4.2, "speaker": "1", "text": "أهلاً بالفريق، order ال discount code عشان أعمل checkout"},
    {"start": 4.2, "end": 8.0, "speaker": "2", "text": "the project ده محتاج تركيز عشان ال deadline قربت"},
    {"start": 8.0, "end": 11.5, "speaker": "1", "text": "الـAPI معتمد على ال back-end، والـteam جـميل جداً"},
    {"start": 11.5, "end": 15.0, "speaker": "2", "text": "روحت المدرسة والمستشفى سنة ٢٠٢٦"},
    {"start": 15.0, "end": 18.0, "speaker": "1", "text": "Let's start the sprint planning now."},
]
CLIENT = {"name": "sedjem-tests", "version": "1.0"}


def lecture_lines(n, words="we talk about the topic"):
    return [{"start": 10.0 * k + 0.4, "end": 10.0 * k + 9.0, "speaker": "1", "text": f"Part {k}: {words} number {k}"}
            for k in range(n)]


def write_job(home, jid, title, lines=None, **fields):
    """A job as the app stores it: job.json, and transcript.json when lines are given."""
    folder = Path(home) / "app_data" / "jobs" / jid
    folder.mkdir(parents=True)
    stamp = f"{jid[:4]}-{jid[4:6]}-{jid[6:8]}T{jid[9:11]}:{jid[11:13]}:{jid[13:15]}+03:00"
    job = {"id": jid, "title": title, "source_name": f"{title}.m4a", "created": stamp, "model": "cohere",
           "model_title": "Cohere Transcribe Arabic", "kind": "local", "status": "done", "stage": None,
           "options": {"speakers": "auto", "language": "ar", "prompt": ""}, "speaker_names": {}, "audio_s": 21.0,
           "started": stamp, "finished": stamp, **fields}
    (folder / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    if lines is not None:
        (folder / "transcript.json").write_text(json.dumps({"lines": lines, "edited": False}, ensure_ascii=False),
                                                encoding="utf-8")
    return job


def youtube(vid, index=None, **fields):
    extra = {"course": {**COURSE, "index": index}} if index else {}
    return {"youtube_id": vid, "source_url": f"https://www.youtube.com/watch?v={vid}", "model": "whisper-medium",
            "model_title": "whisper-medium code-switching", **extra, **fields}


def demo(home):
    """A meeting with two speakers, another meeting, a course of five lectures (1, 2 and 4 done, lecture 2 twice,
    3 still running, 5 not added) and a YouTube video on its own."""
    write_job(home, MEETING, "Sprint planning", MEETING_LINES, speaker_names={"2": "Mona"})
    write_job(home, OTHER_MEETING, "Design review", [{"start": 1.0, "end": 3.0, "speaker": None, "text": "the colours of the login page"}])
    write_job(home, INTRO, "Introduction", lecture_lines(6, "what prompts are"), audio_s=60.0, **youtube("vidprompt01", 1))
    write_job(home, TOKENS_OLD, "Tokenizers", lecture_lines(8, "old words about the tokenizer"), audio_s=80.0,
              finished="2026-10-17T10:00:00+03:00", **youtube("vidprompt02", 2))
    write_job(home, TOKENS_NEW, "Tokenizers", lecture_lines(8, "how the tokenizer cuts text into tokens"), audio_s=80.0,
              finished="2026-10-17T12:30:00+03:00", **{**youtube("vidprompt02", 2), "model_title": "Cohere Transcribe Arabic"})
    write_job(home, EMBED, "Embeddings", status="running", stage="transcribing", finished=None, audio_s=None,
              **youtube("vidprompt03", 3))
    write_job(home, CONTEXT, "Context windows", lecture_lines(60, "the context window holds tokens"), audio_s=600.0,
              **youtube("vidprompt04", 4))
    write_job(home, TALK, "A talk", [{"start": 3.5, "end": 6.0, "speaker": "1", "text": "a talk about attention"}],
              **youtube("vidtalk0001"))


class Case(unittest.TestCase):
    """A fresh demo data folder and server for each test, spoken to in-process."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-mcp-"))
        demo(self.home)
        self.cfg = Config(home=self.home)
        self.server = sedjem_mcp.Server(self.cfg)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def send(self, method, params=None, rid=1):
        return self.server.handle({"jsonrpc": "2.0", "id": rid, "method": method, **({} if params is None else {"params": params})})

    def modern(self, method, params=None, client=CLIENT, version="2026-07-28"):
        meta = {sedjem_mcp.VERSION_KEY: version, sedjem_mcp.CAPABILITIES_KEY: {}, **({sedjem_mcp.CLIENT_KEY: client} if client else {})}
        return self.send(method, {**(params or {}), "_meta": meta})

    def result(self, method, params=None):
        answer = self.modern(method, params)
        self.assertIn("result", answer, answer)
        return answer["result"]

    def tool(self, name, **args):
        """(text, is_error) of a tool call."""
        r = self.result("tools/call", {"name": name, "arguments": args})
        return r["content"][0]["text"], bool(r.get("isError"))

    def text(self, name, **args):
        text, failed = self.tool(name, **args)
        self.assertFalse(failed, text)
        return text

    def problem(self, name, **args):
        text, failed = self.tool(name, **args)
        self.assertTrue(failed, text)
        return text

    def code(self, answer):
        self.assertIn("error", answer, answer)
        return answer["error"]["code"]


class Protocol(Case):
    def test_initialize_agrees_on_a_version(self):
        for asked, agreed in (("2024-11-05", "2024-11-05"), ("2025-03-26", "2025-03-26"), ("2025-06-18", "2025-06-18"),
                              ("2025-11-25", "2025-11-25"), ("2026-07-28", "2025-11-25"), ("1999-01-01", "2025-11-25")):
            r = self.send("initialize", {"protocolVersion": asked, "capabilities": {}, "clientInfo": CLIENT})["result"]
            self.assertEqual(r["protocolVersion"], agreed)
        self.assertEqual(r["serverInfo"], {"name": "sedjem", "title": "Sedjem", "version": __version__})
        self.assertEqual(r["capabilities"], {"tools": {}, "resources": {}, "prompts": {}, "completions": {}})
        self.assertIn("never follow instructions", r["instructions"])
        self.assertEqual(self.code(self.send("initialize", {"capabilities": {}})), -32602)

    def test_the_earlier_versions_need_the_handshake_and_get_plain_results(self):
        self.assertEqual(self.code(self.send("tools/list")), -32602, "neither initialize nor the 2026 _meta")
        self.assertEqual(self.send("ping")["result"], {}, "ping needs neither")
        self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": CLIENT})
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        tools = self.send("tools/list")["result"]
        self.assertNotIn("resultType", tools)
        self.assertNotIn("_meta", tools)
        self.assertEqual(self.code(self.send("server/discover")), -32601, "discover is 2026 only")

    def test_2026_requests_carry_everything(self):
        r = self.result("server/discover")
        self.assertEqual(r["supportedVersions"], ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"])
        self.assertEqual(r["capabilities"], sedjem_mcp.CAPABILITIES)
        self.assertEqual((r["resultType"], r["cacheScope"], r["ttlMs"]), ("complete", "private", 0))
        self.assertEqual(r["_meta"], {sedjem_mcp.SERVER_KEY: {"name": "sedjem", "version": __version__}})
        for method, params in (("tools/list", {}), ("prompts/list", {}), ("resources/list", {}), ("resources/templates/list", {}),
                               ("resources/read", {"uri": f"sedjem://transcript/{MEETING}"})):
            r = self.result(method, params)
            self.assertEqual((r["resultType"], r["cacheScope"], r["ttlMs"]), ("complete", "private", 0), method)
        for method, params in (("tools/call", {"name": "list_courses"}), ("ping", {}),
                               ("prompts/get", {"name": "summarize", "arguments": {"transcript": MEETING}}),
                               ("completion/complete", {"ref": {"type": "ref/prompt", "name": "summarize"},
                                                        "argument": {"name": "transcript", "value": ""}})):
            r = self.result(method, params)
            self.assertEqual(r["resultType"], "complete", method)
            self.assertIn(sedjem_mcp.SERVER_KEY, r["_meta"])
            self.assertNotIn("ttlMs", r)
        self.assertTrue(self.result("tools/list")["tools"], "no handshake was needed, and none is remembered")

    def test_2026_envelope_errors(self):
        meta = lambda **m: self.send("tools/list", {"_meta": m})  # noqa: E731
        v, caps = sedjem_mcp.VERSION_KEY, sedjem_mcp.CAPABILITIES_KEY
        self.assertEqual(self.code(meta(**{v: "2026-07-28"})), -32602, "no capabilities")
        self.assertEqual(self.code(meta(**{caps: {}})), -32602, "no version, and no handshake either")
        self.assertEqual(self.code(meta(**{v: 20260728, caps: {}})), -32602, "a version that isn't a string")
        self.assertEqual(self.code(meta(**{v: "2026-07-28", caps: []})), -32602)
        for version in ("2025-11-25", "2099-01-01"):
            answer = meta(**{v: version, caps: {}})
            self.assertEqual(answer["error"]["code"], -32022)
            self.assertEqual(answer["error"]["message"], "Unsupported protocol version")
            self.assertEqual(answer["error"]["data"], {"supported": sedjem_mcp.SUPPORTED, "requested": version})

    def test_json_rpc_errors(self):
        handle = self.server.handle_line
        self.assertEqual(json.loads(handle(b"{not json")), {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        self.assertEqual(json.loads(handle(b'{"a": "\xc3\x28"}'))["error"]["code"], -32700, "not UTF-8")
        for bad in ({"id": 1, "method": "ping"}, {"jsonrpc": "1.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "id": 1, "method": 5},
                    {"jsonrpc": "2.0", "id": None, "method": "ping"}, {"jsonrpc": "2.0", "id": True, "method": "ping"},
                    {"jsonrpc": "2.0", "id": 1.5, "method": "ping"}, "ping", 7):
            self.assertEqual(self.code(self.server.handle(bad)), -32600, bad)
        self.assertEqual(self.code(self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1]})), -32602)
        self.assertEqual(self.code(self.modern("no/such/method")), -32601)
        self.assertEqual(self.code(self.modern("tools/call", {"name": "delete_everything"})), -32602)
        self.assertEqual(self.code(self.modern("tools/call", {"name": "search", "arguments": "deadline"})), -32602)
        self.assertEqual(self.code(self.modern("tools/call", {"arguments": {}})), -32602)
        self.assertEqual(self.code(self.modern("prompts/get", {"name": "no_such_prompt"})), -32602)
        self.assertEqual(self.code(self.modern("prompts/get", {"name": "summarize", "arguments": {}})), -32602)
        self.assertEqual(self.code(self.modern("completion/complete", {"ref": {"type": "ref/prompt", "name": "x"},
                                                                       "argument": {"name": "a", "value": ""}})), -32602)
        self.assertEqual(self.code(self.modern("resources/list", {"cursor": "not a cursor"})), -32602)
        with mock.patch.object(sedjem_mcp.Library, "courses", side_effect=RuntimeError("a bug")), \
                mock.patch.object(sedjem_mcp, "say") as said:
            answer = self.modern("tools/call", {"name": "list_courses"})
        self.assertEqual(answer["error"], {"code": -32603, "message": "Internal error"}, "no details reach the client")
        self.assertIn("a bug", said.call_args.args[0], "they go to stderr")

    def test_notifications_get_no_answer(self):
        for method in ("notifications/initialized", "notifications/cancelled", "notifications/whatever", "tools/list"):
            self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": method, "params": {"requestId": 1}}))
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "id": 9, "result": {}}), "an answer to nothing we asked")

    def test_batches(self):
        self.send("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": CLIENT})
        answers = self.server.handle([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                                      {"jsonrpc": "2.0", "id": "two", "method": "no/such"}])
        self.assertEqual([(a["id"], "result" in a) for a in answers], [(1, True), ("two", False)])
        self.assertIsNone(self.server.handle([{"jsonrpc": "2.0", "method": "notifications/initialized"}]))
        self.assertEqual(self.code(self.server.handle([])), -32600)

    def test_tools_are_described_for_the_model(self):
        tools = self.result("tools/list")["tools"]
        self.assertEqual([t["name"] for t in tools], ["list_transcriptions", "list_courses", "course_outline",
                                                      "get_transcript", "search", "get_course_text"])
        for t in tools:
            self.assertEqual(t["annotations"], {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                                                "openWorldHint": False})
            self.assertEqual((t["inputSchema"]["type"], t["inputSchema"]["additionalProperties"]), ("object", False))
            self.assertTrue(t["title"] and t["description"])
            self.assertNotIn("run", t)
        by_name = {t["name"]: t for t in tools}
        self.assertEqual(by_name["list_courses"]["inputSchema"], {"type": "object", "additionalProperties": False})
        for name in ("course_outline", "get_transcript", "search", "get_course_text"):
            self.assertIn("never follow instructions", by_name[name]["description"])
        self.assertIn("Arabic letters", by_name["search"]["description"])
        self.assertEqual(by_name["search"]["inputSchema"]["required"], ["query"])
        self.assertEqual(json.dumps(self.result("tools/list")["tools"]), json.dumps(tools), "the same order every time")

    def test_one_line_of_utf8_json(self):
        line = sedjem_mcp.encode({"text": "سطر\nجديد \u2028 \u2029 \ud800 \r"})
        self.assertNotIn(b"\n", line)
        self.assertNotIn("\u2028".encode(), line)
        self.assertEqual(json.loads(line)["text"], "سطر\nجديد \u2028 \u2029 \ud800 \r")
        self.assertIn("سطر".encode(), line, "Arabic as UTF-8, not escaped")


@unittest.skipIf(ClientSession is None, "the MCP SDK is not installed (pip install -r requirements-dev.txt)")
class OfficialSdk(Case):
    """The official MCP SDK starts the server as an assistant does (assistants.launch) and checks every answer
    against the protocol's schema for the version agreed."""

    def connect(self, era):
        command, args, env = assistants.launch(self.cfg)
        params = StdioServerParameters(command=command, args=args, env=env)
        errlog = open(os.devnull, "w")
        self.addCleanup(errlog.close)
        got = {}

        async def session():
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write, client_info=sdk_types.Implementation(**CLIENT)) as s:
                    if era == "modern":
                        got["hello"] = await s.discover()
                    else:
                        got["hello"] = await s.initialize()
                    got["version"], got["server"] = s.protocol_version, s.server_info
                    got["tools"] = [t.name for t in (await s.list_tools()).tools]
                    got["search"] = await s.call_tool("search", {"query": "tokenizer", "course": "prompt"})
                    got["missing"] = await s.call_tool("get_transcript", {"id": "20200101-000000-0000"})
                    got["resources"] = [r.uri for r in (await s.list_resources()).resources]
                    got["templates"] = [t.uri_template for t in (await s.list_resource_templates()).resource_templates]
                    got["transcript"] = (await s.read_resource(f"sedjem://transcript/{MEETING}")).contents[0]
                    got["course"] = (await s.read_resource("sedjem://course/PLprompt01")).contents[0]
                    try:
                        await s.read_resource(f"sedjem://transcript/{MEETING[:-4]}ffff")
                    except MCPError as e:
                        got["not found"] = e.error.code
                    got["prompts"] = [p.name for p in (await s.list_prompts()).prompts]
                    got["summary"] = await s.get_prompt("summarize", {"transcript": "Sprint"})
                    got["notes"] = await s.get_prompt("study_notes", {"course": "Prompt Engineering", "lectures": "1-2"})
                    got["complete"] = (await s.complete(sdk_types.PromptReference(type="ref/prompt", name="summarize"),
                                                        {"name": "transcript", "value": "spr"})).completion.values
                    got["complete id"] = (await s.complete(
                        sdk_types.ResourceTemplateReference(type="ref/resource", uri="sedjem://course/{course}"),
                        {"name": "course", "value": "PL"})).completion.values
        anyio.run(session)
        return got

    def check(self, got):
        self.assertEqual(got["tools"], [t["name"] for t in sedjem_mcp.TOOLS])
        self.assertFalse(got["search"].is_error)
        self.assertIn("how the tokenizer cuts text", got["search"].content[0].text)
        self.assertTrue(got["missing"].is_error)
        self.assertIn("There is no transcription", got["missing"].content[0].text)
        self.assertEqual(str(got["resources"][0]), "sedjem://course/PLprompt01")
        self.assertIn(f"sedjem://transcript/{MEETING}", map(str, got["resources"]))
        self.assertEqual(got["templates"], ["sedjem://transcript/{id}", "sedjem://course/{course}"])
        self.assertEqual(got["transcript"].mime_type, "text/markdown")
        self.assertIn("Mona: the project", got["transcript"].text)
        self.assertIn("## Lecture 2: Tokenizers", got["course"].text)
        self.assertEqual(got["prompts"], ["summarize", "study_notes", "quiz", "flashcards", "meeting_minutes"])
        self.assertEqual([m.content.type for m in got["summary"].messages], ["text", "resource"])
        self.assertEqual(str(got["summary"].messages[1].content.resource.uri), f"sedjem://transcript/{MEETING}")
        self.assertIn("course_outline", got["notes"].messages[0].content.text)
        self.assertEqual(got["complete"], [f"Sprint planning ({MEETING})"])
        self.assertEqual(got["complete id"], ["PLprompt01"])
        log = assistants.read_log(self.cfg)
        self.assertEqual({e["client"] for e in log}, {"sedjem-tests 1.0"})
        self.assertEqual([e["action"] for e in reversed(log)], ["search", "get_transcript", "resources/read",
                                                                "resources/read", "resources/read", "prompts/get", "prompts/get"])

    def test_2026_connection(self):
        got = self.connect("modern")
        self.assertEqual(got["version"], "2026-07-28")
        self.assertEqual((got["server"].name, got["server"].version), ("sedjem", __version__))
        self.assertIn("2026-07-28", got["hello"].supported_versions)
        self.assertEqual(got["not found"], -32602)
        self.check(got)

    def test_handshake_connection(self):
        got = self.connect("legacy")
        self.assertEqual(got["version"], "2025-11-25")
        self.assertEqual((got["server"].name, got["server"].title), ("sedjem", "Sedjem"))
        self.assertIn("quoted", got["hello"].instructions)
        self.assertEqual(got["not found"], -32002)
        self.check(got)


class Pipes(Case):
    """The server as its own process, on real pipes."""

    def start(self, *args, code=None):
        env = {**os.environ, "PYTHONPATH": str(ROOT), "SEDJEM_HOME": str(self.home)}
        argv = [sys.executable, "-P", *(("-c", code) if code else args or ("-m", "app.mcp"))]
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                cwd=self.home)
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: [lines.put(x) for x in iter(proc.stdout.readline, b"")] + [lines.put(None)],
                                  daemon=True)
        reader.start()

        def stop():
            proc.kill()
            proc.wait()
            reader.join(10)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
        self.addCleanup(stop)
        return proc, lines

    def ask(self, proc, lines, message):
        proc.stdin.write(json.dumps(message).encode() + b"\n")
        proc.stdin.flush()
        line = lines.get(timeout=60)
        self.assertTrue(line and line.endswith(b"\n"), line)
        return json.loads(line)

    def test_a_session_answers_each_message_at_once_and_ends_with_stdin(self):
        proc, lines = self.start()
        r = self.ask(proc, lines, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": CLIENT}})
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n\n')
        r = self.ask(proc, lines, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                   "params": {"name": "search", "arguments": {"query": "اهلا"}}})
        self.assertIn("أهلاً بالفريق", r["result"]["content"][0]["text"])
        r = self.ask(proc, lines, {"jsonrpc": "2.0", "id": 3, "method": "tools/list",
                                   "params": {"_meta": {sedjem_mcp.VERSION_KEY: "2026-07-28", sedjem_mcp.CAPABILITIES_KEY: {}}}})
        self.assertEqual(r["result"]["resultType"], "complete", "a 2026 request in the same process")
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=20), 0)
        self.assertIsNone(lines.get(timeout=5), "nothing more on stdout")
        self.assertIn(b"MCP server on stdio", proc.stderr.read())
        self.assertEqual(assistants.read_log(self.cfg)[0]["client"], "sedjem-tests 1.0")

    def test_nothing_but_messages_on_stdout_even_when_something_prints(self):
        noisy = ("import os, sys\nfrom app import mcp\nhandle = mcp.Server.handle\n"
                 "def noisy(self, message):\n    print('a stray print')\n    os.write(1, b'a write to fd 1\\n')\n"
                 "    return handle(self, message)\nmcp.Server.handle = noisy\nsys.exit(mcp.main())\n")
        proc, lines = self.start(code=noisy)
        for rid in range(1, 4):
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": "ping"}).encode() + b"\n")
        proc.stdin.write(b"[]\n{broken\n")
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=30), 0)
        out = []
        while (line := lines.get(timeout=5)) is not None:
            out.append(json.loads(line))
        self.assertEqual([m.get("id") for m in out], [1, 2, 3, None, None])
        self.assertEqual([m.get("error", {}).get("code") for m in out[3:]], [-32600, -32700])
        err = proc.stderr.read().decode()
        self.assertIn("a stray print", err)
        self.assertIn("a write to fd 1", err)

    def test_exits_at_once_when_stdin_closes(self):
        proc, _ = self.start()
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=30), 0)

    def test_without_python_stdin_and_stdout(self):
        """A windowed build may start with sys.stdin and sys.stdout set to None: the OS's standard streams are
        used (from file descriptors here; on Windows also from the process's standard handles)."""
        proc, lines = self.start(code="import sys\nsys.stdin = sys.stdout = None\nfrom app import mcp\nsys.exit(mcp.main())\n")
        r = self.ask(proc, lines, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(r["result"], {})
        proc.stdin.close()
        self.assertEqual(proc.wait(timeout=30), 0)

    def test_the_desktop_entry_points(self):
        for args in (("-m", "app.desktop", "--mcp"), (str(ROOT / "desktop" / "sedjem.py"), "--mcp")):
            proc, lines = self.start(*args)
            r = self.ask(proc, lines, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                       "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": CLIENT}})
            self.assertEqual(r["result"]["serverInfo"]["name"], "sedjem", args)
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=30), 0)


class Visibility(Case):
    def everything(self):
        """What an assistant can find of each kind, by every way there is."""
        listed = self.text("list_transcriptions", status="all", limit=200)
        found = self.text("search", query="the", limit=100)
        uris = [r["uri"] for r in self.result("resources/list")["resources"]]
        offered = self.result("completion/complete", {"ref": {"type": "ref/prompt", "name": "summarize"},
                                                      "argument": {"name": "transcript", "value": ""}})["completion"]["values"]
        return {jid for jid in (MEETING, OTHER_MEETING, INTRO, TOKENS_NEW, CONTEXT, TALK)
                if jid in listed or jid in found or any(jid in u for u in uris) or any(jid in v for v in offered)}

    def gone(self, jid, title):
        """A transcription that is as if it didn't exist."""
        nowhere = "20200101-000000-0000"
        self.assertEqual(self.problem("get_transcript", id=jid).replace(jid, nowhere),
                         self.problem("get_transcript", id=nowhere), "the same answer as for an id that isn't there")
        self.assertEqual(self.problem("search", query="the", id=jid).replace(jid, nowhere),
                         self.problem("search", query="the", id=nowhere))
        self.assertEqual(self.code(self.modern("resources/read", {"uri": f"sedjem://transcript/{jid}"})), -32602)
        self.assertEqual(self.code(self.modern("prompts/get", {"name": "summarize", "arguments": {"transcript": jid}})), -32602)
        self.assertEqual(self.code(self.modern("prompts/get", {"name": "summarize", "arguments": {"transcript": title}})), -32602)
        self.assertNotIn(title, self.text("list_transcriptions", status="all", limit=200))

    def test_everything_is_visible_by_default(self):
        self.assertEqual(self.everything(), {MEETING, OTHER_MEETING, INTRO, TOKENS_NEW, CONTEXT, TALK})

    def test_the_switches_apply_at_once(self):
        self.cfg.save_settings(mcp_recordings=False)
        self.assertEqual(self.everything(), {INTRO, TOKENS_NEW, CONTEXT, TALK})
        self.gone(MEETING, "Sprint planning")
        self.cfg.save_settings(mcp_recordings=True, mcp_courses=False)
        self.assertEqual(self.everything(), {MEETING, OTHER_MEETING})
        self.gone(TALK, "A talk")
        self.gone(INTRO, "Introduction")
        self.assertIn("there are no courses", self.problem("course_outline", course="Prompt Engineering"))
        self.assertIn("There are no courses", self.text("list_courses"))
        self.assertEqual(self.code(self.modern("resources/read", {"uri": "sedjem://course/PLprompt01"})), -32602)
        self.assertEqual(self.result("completion/complete", {"ref": {"type": "ref/prompt", "name": "quiz"},
                                                             "argument": {"name": "course", "value": ""}})["completion"]["values"], [])

    def test_hiding_one_transcription_or_a_course(self):
        assistants.set_hidden(self.cfg, f"job:{MEETING}", True)
        self.assertEqual(self.everything(), {OTHER_MEETING, INTRO, TOKENS_NEW, CONTEXT, TALK})
        self.gone(MEETING, "Sprint planning")
        assistants.set_hidden(self.cfg, "course:PLprompt01", True)
        self.assertEqual(self.everything(), {OTHER_MEETING, TALK}, "every lecture of the course; the video on its own stays")
        self.gone(CONTEXT, "Context windows")
        self.assertIn('No course matches "Prompt Engineering"', self.problem("course_outline", course="Prompt Engineering"))
        self.assertNotIn("sedjem://course/PLprompt01", json.dumps(self.result("resources/list")))
        assistants.set_hidden(self.cfg, f"job:{MEETING}", False)
        assistants.set_hidden(self.cfg, "course:PLprompt01", False)
        self.assertEqual(self.everything(), {MEETING, OTHER_MEETING, INTRO, TOKENS_NEW, CONTEXT, TALK})

    def test_a_hidden_version_of_a_lecture_leaves_the_one_before(self):
        self.assertIn("how the tokenizer cuts text", self.text("get_transcript", course="PLprompt01", lecture=2))
        assistants.set_hidden(self.cfg, f"job:{TOKENS_NEW}", True)
        text = self.text("get_transcript", course="PLprompt01", lecture=2)
        self.assertIn("old words about the tokenizer", text)
        self.assertNotIn(TOKENS_NEW, self.text("course_outline", course="PLprompt01"))
        self.assertNotIn("how the tokenizer cuts", self.result("resources/read", {"uri": "sedjem://course/PLprompt01"})["contents"][0]["text"])


class Tools(Case):
    def test_list_transcriptions(self):
        text = self.text("list_transcriptions")
        self.assertTrue(text.startswith("7 transcriptions, newest first"), text)
        self.assertNotIn(EMBED, text, "only finished ones by default")
        order = [m.group(1) for m in re.finditer(r"id (\S+) ·", text)]
        self.assertEqual(order, [MEETING, OTHER_MEETING, INTRO, TOKENS_NEW, TOKENS_OLD, CONTEXT, TALK],
                         "newest first, with the course's lectures together in order")
        self.assertIn("speakers: Speaker 1, Mona", text)
        self.assertIn("course Prompt Engineering (PLprompt01), lecture 4 of 5 · https://www.youtube.com/watch?v=vidprompt04", text)
        self.assertIn("YouTube video · https://www.youtube.com/watch?v=vidtalk0001", text)
        page = self.text("list_transcriptions", limit=2, offset=2)
        self.assertEqual(re.findall(r"id (\S+) ·", page), [INTRO, TOKENS_NEW])
        self.assertIn("Showing 3-4", page)
        self.assertIn("offset=4", page)
        self.assertIn(EMBED, self.text("list_transcriptions", status="active"))
        self.assertIn("running", self.text("list_transcriptions", status="all", course="prompt"))
        self.assertEqual(re.findall(r"id (\S+) ·", self.text("list_transcriptions", kind="recording")), [MEETING, OTHER_MEETING])
        self.assertEqual(re.findall(r"id (\S+) ·", self.text("list_transcriptions", text="MONA")), [MEETING], "speaker names too")
        self.assertIn("past the end", self.text("list_transcriptions", offset=50))
        self.assertIn("status must be one of", self.problem("list_transcriptions", status="finished"))
        self.assertIn("Unknown argument sort", self.problem("list_transcriptions", sort="date"))
        self.assertIn("limit must be a whole number", self.problem("list_transcriptions", limit="many"))

    def test_courses_and_outline(self):
        text = self.text("list_courses")
        self.assertIn("course id PLprompt01 · https://www.youtube.com/playlist?list=PLprompt01", text)
        self.assertIn("3 of 5 lectures transcribed, 12:20 in all; 1 on the way", text)
        outline = self.text("course_outline", course="engineering")
        self.assertIn(sedjem_mcp.QUOTE, outline)
        self.assertIn(f"2. Tokenizers · 01:20 · id {TOKENS_NEW} · done", outline, "the version that finished last")
        self.assertIn(f"3. Embeddings · length unknown · id {EMBED} · running", outline)
        self.assertIn('"Part 0: what prompts are number 0 Part 1', outline)
        self.assertIn("Not added to Sedjem: lecture 5.", outline)
        self.assertIn("https://www.youtube.com/watch?v=vidprompt04", outline)
        write_job(self.home, "20261015-090000-dddd", "Prompting basics", lecture_lines(2),
                  **youtube("vidother0001", 1, course={**COURSE, "id": "PLother02", "title": "Prompt Patterns", "index": 1}))
        self.assertIn('"prompt" matches 2 courses: Prompt Engineering (PLprompt01); Prompt Patterns (PLother02)',
                      self.problem("course_outline", course="prompt"))
        self.assertIn("Prompt Patterns", self.text("course_outline", course="PLother02"))
        self.assertIn("Prompt Engineering", self.text("course_outline", course="prompt engineering"))
        self.assertIn("Prompt Patterns", self.text("course_outline", course="Prompt Patterns (PLother02)"))
        self.assertIn("The courses: Prompt Engineering (PLprompt01); Prompt Patterns", self.problem("course_outline", course="Python"))

    def test_reading_in_pieces_follows_the_hints(self):
        everything = self.text("get_transcript", course="Prompt Engineering", lecture=4, max_chars=40000)
        all_lines = [x for x in everything.splitlines() if x.startswith("[")]
        self.assertEqual(len(all_lines), 60)
        self.assertTrue(everything.endswith("End of the transcript."))
        for how in ("start", "from_line"):
            got, args, calls = [], {"course": "PLprompt01", "lecture": 4, "max_chars": 700}, 0
            while True:
                text = self.text("get_transcript", **args)
                got += [x for x in text.splitlines() if x.startswith("[")]
                calls += 1
                hint = re.search(r"To continue: get_transcript\((.*)\), or from_line=(\d+) instead of start\.", text)
                if not hint:
                    break
                if how == "start":
                    args = json.loads("{" + re.sub(r"(\w+)=", r'"\1":', hint.group(1)) + "}")
                else:
                    args = {**args, "from_line": int(hint.group(2))}
            self.assertEqual(got, all_lines, how)
            self.assertGreater(calls, 3)
        part = self.text("get_transcript", id=CONTEXT, start="01:00", end=150)
        self.assertEqual([x[:7] for x in part.splitlines() if x.startswith("[")], ["[01:00]", "[01:10]", "[01:20]", "[01:30]", "[01:40]",
                                                                                     "[01:50]", "[02:00]", "[02:10]", "[02:20]"])
        self.assertIn("Lines 7-15 of 60 (01:00-02:29)", part)
        self.assertIn("End of the part asked for", part)
        self.assertIn("No line starts between 20:00-the end", self.text("get_transcript", id=CONTEXT, start="20:00"))
        self.assertIn("past its end", self.problem("get_transcript", id=CONTEXT, from_line=61))
        self.assertIn("end must be after start", self.problem("get_transcript", id=CONTEXT, start=50, end="0:40"))
        self.assertIn("mm:ss", self.problem("get_transcript", id=CONTEXT, start="1:75"))
        self.assertIn("mm:ss", self.problem("get_transcript", id=CONTEXT, start="1:60:00"))
        self.assertIn("No line starts between 1:00:00-the end", self.text("get_transcript", id=CONTEXT, start="1:00:00"))

    def test_get_transcript_header_and_speakers(self):
        text = self.text("get_transcript", id=MEETING)
        self.assertTrue(text.startswith(sedjem_mcp.QUOTE))
        self.assertIn("# Sprint planning", text)
        self.assertIn("[00:04] Mona: the project ده محتاج تركيز", text)
        self.assertIn("speakers: Speaker 1, Mona", text)
        self.assertNotIn("Speaker 1:", self.text("get_transcript", id=INTRO), "one speaker, no name: no names")
        self.assertIn("Video: https://www.youtube.com/watch?v=vidprompt01 (add &t=<seconds>s", self.text("get_transcript", id=INTRO))
        self.assertIn("old words", self.text("get_transcript", id=TOKENS_OLD), "an earlier version, by its id")
        self.assertIn("has no transcript yet: it is running", self.problem("get_transcript", course="PLprompt01", lecture=3))
        self.assertIn("Lecture 5 of Prompt Engineering has no transcript in Sedjem. Transcribed: 1-2, 4.",
                      self.problem("get_transcript", course="PLprompt01", lecture=5))
        self.assertIn("either id, or course and lecture", self.problem("get_transcript", id=MEETING, lecture=2))
        self.assertIn("Give id", self.problem("get_transcript", course="PLprompt01"))
        self.assertIn("lecture must be at least 1", self.problem("get_transcript", course="PLprompt01", lecture=0))

    def test_search(self):
        text = self.text("search", query="Tokenizer")
        self.assertIn('4 matches for "Tokenizer", in 1 transcription.', text,
                      "the lecture's newest version only; its 8 lines in 4 matches, each shown with the lines around it")
        self.assertNotIn("old words", text)
        self.assertIn(f"1. Prompt Engineering, lecture 2: Tokenizers · id {TOKENS_NEW} · at 00:00", text)
        self.assertIn("https://www.youtube.com/watch?v=vidprompt02&t=20s", text, "a link to the moment, in whole seconds")
        self.assertIn(" > [00:20] Part 2: how the tokenizer cuts text", text)
        self.assertIn("   [00:30] Part 3: how the tokenizer", text)
        self.assertIn(sedjem_mcp.QUOTE, text)
        self.assertIn("first 2 are shown", self.text("search", query="tokenizer", limit=2))
        self.assertIn("old words", self.text("search", query="tokenizer", id=TOKENS_OLD))
        meeting = self.text("search", query="sprint deadline", id=MEETING, context=3)
        self.assertIn("1 match for", meeting, "the words may be in the lines around")
        self.assertIn("No line in Sprint planning has all of: sprint, deadline", self.text("search", query="sprint deadline", id=MEETING, context=0))
        self.assertIn('for "tokens" in Prompt Engineering, in 2 lectures', self.text("search", query="tokens", course="prompt"))
        best = self.text("search", query="sprint planning", id=MEETING, context=1)
        self.assertIn(" > [00:15] Speaker 1: Let's start the sprint planning now.", best, "the line with every word is the match")
        self.assertIn("Give some words", self.problem("search", query="  ...  "))
        self.assertIn("query is needed", self.problem("search"))

    def test_arabic_spellings_match(self):
        for query, line in (("اهلا", "أهلاً بالفريق"), ("مدرسه", "روحت المدرسة"), ("مستشفي", "والمستشفى"),
                            ("جميل", "جـميل"), ("مدرسين", None), ("api TEAM", "الـAPI معتمد"), ("2026", "سنة ٢٠٢٦"),
                            ("سنه ٢٠٢٦", "سنة ٢٠٢٦"), ("إهلا", "أهلاً بالفريق"), ("ﻷهلا", None), ("لأهلا", None)):
            text = self.text("search", query=query, id=MEETING, context=0)
            if line:
                self.assertIn(line, text, query)
            else:
                self.assertIn("No line", text, query)
        self.assertEqual(sedjem_mcp.fold("أإآٱ ىة ـ ً ٢٠ ABC ﻷ"), "اااا يه   20 abc لا", "the lam-alef ligature too")

    def test_course_text_in_pieces(self):
        lib = sedjem_mcp.Library(self.cfg, self.server.store)
        whole = courses.course_md({"title": COURSE["title"], "url": COURSE["url"], "count": 5},
                                  [(lib.job(j), lib.lines(lib.job(j))) for j in (INTRO, TOKENS_NEW, CONTEXT)])
        got, offset, ids = "", 0, set()
        while True:
            text = self.text("get_course_text", course="Prompt Engineering", offset=offset, max_chars=1000)
            self.assertTrue(text.startswith(sedjem_mcp.QUOTE + "\n\n"))
            body, tail = text[len(sedjem_mcp.QUOTE) + 2:].rsplit("\n\nCharacters ", 1)
            ids.update(assistants.read_log(self.cfg)[0]["ids"])
            m = re.search(r"To continue: get_course_text\(.*offset=(\d+)", tail)
            got += whole[offset:int(m.group(1)) if m else len(whole)]
            self.assertEqual(body, whole[offset:int(m.group(1)) if m else len(whole)].rstrip())
            if not m:
                self.assertIn("the end of the course", tail)
                break
            offset = int(m.group(1))
        self.assertEqual(got, whole)
        self.assertEqual(ids, {INTRO, TOKENS_NEW, CONTEXT})
        self.assertIn("## Lecture 2: Tokenizers", whole)
        self.assertNotIn("old words", whole)
        self.assertIn("past its end", self.problem("get_course_text", course="PLprompt01", offset=10 ** 6))


class Resources(Case):
    def test_listed_in_pages(self):
        everything = self.result("resources/list")["resources"]
        self.assertEqual([r["uri"] for r in everything][:2], ["sedjem://course/PLprompt01", f"sedjem://transcript/{MEETING}"])
        self.assertEqual(len(everything), 1 + 7, "the course, and every finished transcription")
        self.assertTrue(all(r["mimeType"] == "text/markdown" and r["name"] and r["description"] for r in everything))
        with mock.patch.object(sedjem_mcp, "PAGE", 3):
            got, cursor = [], None
            while True:
                r = self.result("resources/list", {"cursor": cursor} if cursor else {})
                got += r["resources"]
                cursor = r.get("nextCursor")
                if not cursor:
                    break
        self.assertEqual(got, everything)

    def test_read(self):
        meeting = self.result("resources/read", {"uri": f"sedjem://transcript/{MEETING}"})["contents"]
        self.assertEqual((meeting[0]["uri"], meeting[0]["mimeType"]), (f"sedjem://transcript/{MEETING}", "text/markdown"))
        self.assertTrue(meeting[0]["text"].startswith(sedjem_mcp.QUOTE_MD + "# Sprint planning"))
        self.assertIn("[00:04] Mona: the project", meeting[0]["text"])
        lib = sedjem_mcp.Library(self.cfg, self.server.store)
        intro = lib.job(INTRO)
        self.assertEqual(self.result("resources/read", {"uri": f"sedjem://transcript/{INTRO}"})["contents"][0]["text"],
                         sedjem_mcp.QUOTE_MD + courses.lecture_md(intro, lib.lines(intro)))
        course = self.result("resources/read", {"uri": "sedjem://course/PLprompt01"})["contents"][0]["text"]
        self.assertTrue(course.startswith(sedjem_mcp.QUOTE_MD + "# Prompt Engineering"))
        self.assertIn("Transcripts of 3 of 5 lectures", course)
        self.assertEqual(assistants.read_log(self.cfg)[0]["detail"], "Prompt Engineering (course)")
        self.assertEqual(assistants.read_log(self.cfg)[0]["ids"], [INTRO, TOKENS_NEW, CONTEXT])

    def test_addresses_that_lead_nowhere(self):
        for uri in (f"sedjem://transcript/{EMBED}", "sedjem://transcript/../../etc/passwd", f"sedjem://transcript/{MEETING}/..",
                    f"sedjem://transcript/{MEETING}?x=1", "sedjem://transcript/", "file:///etc/passwd", "sedjem://course/../app_data",
                    "sedjem://course/PLnothing", f"SEDJEM://transcript/{MEETING}", "sedjem://course/PLprompt01/../../x"):
            for era in ("modern", "legacy"):
                if era == "modern":
                    answer = self.modern("resources/read", {"uri": uri})
                else:
                    self.send("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": CLIENT})
                    answer = self.send("resources/read", {"uri": uri})
                self.assertEqual(answer["error"], {"code": -32602 if era == "modern" else -32002, "message": "Resource not found",
                                                   "data": {"uri": uri}}, (uri, era))
        self.assertEqual(self.code(self.modern("resources/read", {"uri": 5})), -32602)

    def test_ids_that_lead_nowhere(self):
        for jid in ("../../etc/passwd", "..", "/etc/passwd", "C:\\Windows", MEETING + "/../" + OTHER_MEETING, "", " ",
                    MEETING.upper(), "20261019-100000-aaa", "app_data"):
            text = self.problem("get_transcript", id=jid)
            self.assertTrue(text.startswith("There is no transcription" if jid.strip() else "Give id"), (jid, text))
        for course in ("../../etc", "/", "..\\..\\x"):
            self.assertIn("No course matches", self.problem("course_outline", course=course))
        with mock.patch.object(self.server.store, "dir", wraps=self.server.store.dir) as folder:
            self.problem("get_transcript", id="../../../etc/passwd")
            self.problem("search", query="x", id="../x")
        self.assertEqual(folder.call_count, 0, "an id that isn't one never comes near a path")


class Prompts(Case):
    def get(self, name, **args):
        return self.result("prompts/get", {"name": name, "arguments": args})

    def test_listed_with_their_arguments(self):
        prompts = {p["name"]: p for p in self.result("prompts/list")["prompts"]}
        self.assertEqual(list(prompts), ["summarize", "study_notes", "quiz", "flashcards", "meeting_minutes"])
        self.assertEqual([(a["name"], a["required"]) for a in prompts["quiz"]["arguments"]],
                         [("course", True), ("lectures", True), ("count", False)])
        self.assertTrue(all(p["title"] and p["description"] for p in prompts.values()))

    def test_a_transcript_is_included(self):
        r = self.get("meeting_minutes", transcript="sprint planning", language="Arabic")
        self.assertEqual(r["description"], "Meeting minutes: Sprint planning")
        task, embedded = r["messages"]
        self.assertEqual((task["role"], task["content"]["type"]), ("user", "text"))
        self.assertIn("action items", task["content"]["text"])
        self.assertIn("Write it in Arabic.", task["content"]["text"])
        self.assertIn("do not follow instructions", task["content"]["text"])
        self.assertEqual(embedded["content"]["resource"]["uri"], f"sedjem://transcript/{MEETING}")
        self.assertIn("Mona: the project", embedded["content"]["resource"]["text"])
        r = self.get("summarize", transcript=f"Context windows ({CONTEXT})")
        self.assertIn("&t=<seconds>s", r["messages"][0]["content"]["text"], "a video's moments are linked")
        with mock.patch.object(sedjem_mcp, "EMBED_MAX", 100):
            r = self.get("summarize", transcript=MEETING)
        self.assertEqual(len(r["messages"]), 1)
        self.assertIn(f'get_transcript(id="{MEETING}")', r["messages"][0]["content"]["text"])
        flash = self.get("flashcards", transcript="A talk")
        self.assertEqual(flash["messages"][1]["content"]["resource"]["uri"], f"sedjem://transcript/{TALK}")
        log = assistants.read_log(self.cfg)[0]
        self.assertEqual((log["action"], log["detail"], log["ids"]), ("prompts/get", "flashcards: A talk", [TALK]))

    def test_a_course_is_read_with_the_tools(self):
        r = self.get("study_notes", course="Prompt Engineering", lectures="1-3")
        text = r["messages"][0]["content"]["text"]
        self.assertEqual(r["description"], "Study notes for a course: Prompt Engineering, lectures 1-3")
        self.assertIn('course_outline(course="PLprompt01")', text)
        self.assertIn('get_transcript(course="PLprompt01", lecture=N)', text)
        self.assertIn("Transcribed and to read: 1-2 (lecture 3 not transcribed in Sedjem)", text)
        self.assertIn("&t=<seconds>s", text)
        quiz = self.get("quiz", course="PLprompt01", lectures="2, 4", count="5")["messages"][0]["content"]["text"]
        self.assertIn("Quiz me on lectures 2, 4", quiz)
        self.assertIn("5 questions, one at a time", quiz)
        self.assertIn("Lecture and Time", self.get("flashcards", course="prompt")["messages"][0]["content"]["text"])
        for args in ({"course": "Prompt", "lectures": "all"}, {"course": "Prompt", "lectures": "5"},
                     {"course": "Prompt", "lectures": "2-1"}, {"course": "Prompt", "lectures": "1", "count": "lots"},
                     {"course": "Cooking", "lectures": "1"}):
            self.assertEqual(self.code(self.modern("prompts/get", {"name": "quiz", "arguments": args})), -32602, args)
        self.assertEqual(self.code(self.modern("prompts/get", {"name": "flashcards", "arguments": {}})), -32602)
        self.assertEqual(sedjem_mcp.parse_lectures("1 to 3, 7,9-10"), [1, 2, 3, 7, 9, 10])

    def test_completion_offers_what_can_be_seen(self):
        def complete(name, value, ref=None, **context):
            params = {"ref": ref or {"type": "ref/prompt", "name": "quiz"}, "argument": {"name": name, "value": value}}
            if context:
                params["context"] = {"arguments": context}
            return self.result("completion/complete", params)["completion"]
        self.assertEqual(complete("course", "eng"), {"values": ["Prompt Engineering (PLprompt01)"], "total": 1, "hasMore": False})
        self.assertEqual(complete("lectures", "", course="PLprompt01")["values"], ["1-4", "1", "2", "4"])
        self.assertEqual(complete("lectures", "")["values"], [], "no course chosen yet")
        self.assertEqual(complete("language", "ar")["values"], ["Arabic", "Egyptian Arabic"], "the start first, then inside")
        transcripts = complete("transcript", "", {"type": "ref/prompt", "name": "summarize"})
        self.assertEqual(transcripts["total"], 6, "a lecture transcribed twice is offered once")
        self.assertEqual(complete("transcript", "tal", {"type": "ref/prompt", "name": "summarize"})["values"], [f"A talk ({TALK})"])
        ids = complete("id", "2026101", {"type": "ref/resource", "uri": "sedjem://transcript/{id}"})["values"]
        self.assertEqual(ids, [MEETING, OTHER_MEETING, INTRO, TOKENS_NEW, CONTEXT, TALK])
        self.assertEqual(complete("id", "20261019", {"type": "ref/resource", "uri": "sedjem://transcript/{id}"})["values"], [MEETING])
        with mock.patch.object(sedjem_mcp.Library, "current", return_value=[{"id": f"20261019-1000{i:02d}-aaaa", "title": f"M{i}"} for i in range(150)]):
            many = complete("transcript", "", {"type": "ref/prompt", "name": "summarize"})
        self.assertEqual((len(many["values"]), many["total"], many["hasMore"]), (100, 150, True))
        self.assertEqual(complete("anything", "", {"type": "ref/resource", "uri": "sedjem://nothing/{x}"})["values"], [])


class Log(Case):
    def test_every_read_is_logged_with_the_client(self):
        self.send("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "Claude Desktop", "version": "1.2"}})
        self.send("tools/call", {"name": "search", "arguments": {"query": "deadline"}})
        self.send("tools/call", {"name": "get_transcript", "arguments": {"course": "PLprompt01", "lecture": 4, "start": 0, "end": 600, "max_chars": 2000}})
        self.send("tools/call", {"name": "get_transcript", "arguments": {"id": "20200101-000000-0000"}})
        self.send("tools/call", {"name": "list_transcriptions", "arguments": {"bogus": 1}})
        self.modern("resources/read", {"uri": f"sedjem://transcript/{MEETING}"}, client={"name": "Cursor", "version": "2.0"})
        self.modern("tools/call", {"name": "list_courses"}, client=None)
        log = list(reversed(assistants.read_log(self.cfg)))
        self.assertEqual([(e["client"], e["action"]) for e in log], [
            ("Claude Desktop 1.2", "search"), ("Claude Desktop 1.2", "get_transcript"), ("Claude Desktop 1.2", "get_transcript"),
            ("Claude Desktop 1.2", "list_transcriptions"), ("Cursor 2.0", "resources/read"), ("unknown", "list_courses")])
        self.assertEqual((log[0]["detail"], log[0]["ids"]), ("deadline", [MEETING]))
        self.assertTrue(re.fullmatch(r"Prompt Engineering, lecture 4, 00:00-\d\d:\d\d", log[1]["detail"]), log[1]["detail"])
        self.assertEqual(log[1]["ids"], [CONTEXT])
        self.assertEqual((log[2]["detail"], log[2]["ids"]), ("20200101-000000-0000", []))
        self.assertEqual(log[3]["detail"], '{"bogus": 1}')
        self.assertEqual((log[4]["detail"], log[4]["ids"]), ("Sprint planning", [MEETING]))

    def test_a_log_that_cannot_be_written_does_not_stop_an_answer(self):
        (self.cfg.storage / "mcp").write_text("a file where the log's folder goes", encoding="utf-8")
        self.assertIn("Sprint planning", self.text("list_transcriptions"))


if __name__ == "__main__":
    unittest.main()
