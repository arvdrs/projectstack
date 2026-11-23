"""
Server-metric screenshots attached to a run: storage and validation, what the vision
model is told about the test (UTC and local clock), the AI reading with its cache,
reports, the providers' image payloads and the choice of vision-capable providers.
AI providers and HTTP are replaced by fakes; nothing here calls a real model.
"""

import base64
import io
import json
import re
import threading

import pytest

from projectstack.llm import registry
from projectstack.llm.base import Completion, Image, LLMConfigError, LLMError, Provider
from projectstack.results import highlights, screenshots
from projectstack.results.narrative import from_rules
from projectstack.results.report import render
from tests.test_results_highlights import stepped_doc

PIL = pytest.importorskip("PIL.Image")


def png(w=40, h=20, color=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    PIL.new("RGB", (w, h), color).save(out, "PNG")
    return out.getvalue()


def jpeg(w=40, h=20) -> bytes:
    out = io.BytesIO()
    PIL.new("RGB", (w, h), (10, 120, 200)).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def run(tmp_path):
    folder = tmp_path / "runs" / "20261112-065424-93ae"
    folder.mkdir(parents=True)
    (folder / "findings.json").write_text(json.dumps(stepped_doc()))
    return folder


class FakeVision(Provider):
    """Answers like a vision model: a request with pictures gets an entry per picture, looked up by the
    file name the prompt gives for it ("Image 2: … (file mem.png"); one without pictures is the summary.
    Files in `fail` make their whole request fail, like a provider that does not answer."""
    name = "fake"
    default_model = "eye-1"

    def __init__(self, shots=None, summary=None, fail=()):
        super().__init__({}, None)
        self.shots, self.fail, self.calls = dict(shots or SHOTS), set(fail), []
        self.summary = summary or SUMMARY
        self.lock = threading.Lock()
        self.warmed = 0

    def warm_up(self):
        self.warmed += 1

    def complete(self, system, prompt, *, max_tokens=8192, json_schema=None, effort=None, images=None):
        with self.lock:
            self.calls.append({"system": system, "prompt": prompt, "schema": json_schema, "images": images,
                               "max_tokens": max_tokens})
        if not images:
            return Completion(json.dumps(self.summary), "fake", "eye-1", "end_turn", 300, 100)
        named = re.findall(r"^Image (\d+): .*\(file ([^,)]+)", prompt, re.M)
        if any(name in self.fail for _, name in named):
            raise LLMError("The provider did not answer")
        entries = [{"image": int(n), **self.shots[name]} for n, name in named if name in self.shots]
        return Completion(json.dumps({"screenshots": entries + [{"image": 99, "title": "not attached"}]}),
                          "fake", "eye-1", "end_turn", 900, 300)

    def batches(self):
        return [c for c in self.calls if c["images"]]


def shot_entry(title, **kw):
    return {"title": title, "readable": True, "time_axis": "13:30 to 15:10", "test_on_chart": "13:39 to 14:58",
            "covers_test": "yes", "clock": "local", "series": [], "observations": [f"{title} looked fine."],
            "caveat": "", **kw}


SHOTS = {
    "cpu.png": shot_entry("CPU, app servers", time_axis="13:30 to 15:10, Asia/Kolkata", covers_test="sometimes",
                          observations=["CPU rose with each load step to about 60 percent.", "", "It fell when the test ended."]),
    "mem.png": shot_entry("Memory used, app-server-1", observations=["Memory stayed near 45 percent."]),
}
SUMMARY = {"observations": ["Both servers kept CPU headroom at 500 users."], "caveats": []}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def test_attach_caption_and_remove(run):
    assert screenshots.sniff(png()) == ("image/png", "png") and screenshots.sniff(jpeg())[1] == "jpg"
    assert screenshots.sniff(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None      # no SVG: it can carry script

    a = screenshots.add(run, "C:\\Users\\me\\Desktop\\cpu.png", png(64, 32), "  CPU –\n app servers ")
    b = screenshots.add(run, "mem.jpg", jpeg())
    assert a["name"] == "cpu.png" and a["caption"] == "CPU – app servers"              # path dropped, spaces folded
    assert (a["width"], a["height"], a["media_type"]) == (64, 32, "image/png") and b["file"].endswith(".jpg")
    assert [i["id"] for i in screenshots.list_items(run)] == [a["id"], b["id"]]
    data, media = screenshots.image(run, a["id"])
    assert data == png(64, 32) and media == "image/png"

    screenshots.update(run, b["id"], "x" * 500)
    assert len(screenshots.list_items(run)[1]["caption"]) == screenshots.MAX_CAPTION
    screenshots.remove(run, a["id"])
    assert [i["id"] for i in screenshots.list_items(run)] == [b["id"]]
    assert not (run / "screenshots" / a["file"]).exists()
    for bad in (a["id"], "../findings", "0" * 12):
        with pytest.raises(KeyError):
            screenshots.image(run, bad)


def test_bad_uploads_are_refused(run, monkeypatch):
    with pytest.raises(screenshots.ScreenshotError, match="not a PNG"):
        screenshots.add(run, "notes.txt", b"hello")
    with pytest.raises(screenshots.ScreenshotError, match="empty"):
        screenshots.add(run, "x.png", b"")
    with pytest.raises(screenshots.ScreenshotError, match="cannot be read"):
        screenshots.add(run, "broken.png", png()[:40])                                 # right header, cut short
    monkeypatch.setattr(screenshots, "MAX_BYTES", 100)
    with pytest.raises(screenshots.ScreenshotError, match="larger than"):
        screenshots.add(run, "big.png", png(400, 400, (1, 2, 3)) + b"\0" * 200)
    monkeypatch.setattr(screenshots, "MAX_BYTES", 10 * 1024 * 1024)
    monkeypatch.setattr(screenshots, "MAX_FILES", 2)
    screenshots.add(run, "1.png", png())
    screenshots.add(run, "2.png", png())
    with pytest.raises(screenshots.ScreenshotError, match="up to 2"):
        screenshots.add(run, "3.png", png())
    assert len(screenshots.list_items(run)) == 2


def test_large_pictures_are_scaled_down_for_sending(run):
    big = png(4000, 1000)
    screenshots.add(run, "wide.png", big)
    [image] = screenshots._for_sending(run, screenshots.list_items(run))
    with PIL.open(io.BytesIO(image.data)) as im:
        assert max(im.size) == screenshots.SEND_EDGES[0] and im.size[1] == 512
    assert screenshots.list_items(run)[0]["width"] == 4000                          # the stored file is untouched


# ---------------------------------------------------------------------------
# What the model is told
# ---------------------------------------------------------------------------

def test_test_facts_give_utc_and_local_clocks():
    doc = stepped_doc()
    f = screenshots.test_facts(doc, {"region": "us-east-1"}, "Asia/Kolkata")
    w = f["window"]
    assert w["start_utc"].startswith("2026-10-28T08:09") and w["local_time_zone"] == "Asia/Kolkata (UTC+05:30)"
    assert (w["start_local"], w["end_local"]) == ("2026-10-28 13:39", "2026-10-28 14:58")
    assert f["steady_state"]["from"] == {"utc": "08:29", "local": "13:59"}
    assert f["ramp_up_steps"][0] == {"users": 49, "from": {"utc": "08:10", "local": "13:40"}}
    t = f["timeline"]
    assert t["columns"] == ["utc", "local", "users", "requests_per_s", "p95_ms", "error_pct"]
    assert t["rows"][0] == ["08:09", "13:39", 50, 11.0, 250, 0.0] and len(t["rows"]) == 80

    plain = screenshots.test_facts(doc, None, "Not/AZone")                          # unknown zone: UTC only
    assert "local_time_zone" not in plain["window"] and plain["timeline"]["columns"][:2] == ["utc", "users"]

    long = stepped_doc()
    long["timeline"]["points"] = [{**p, "t_offset_s": 60 * i} for i, p in enumerate(long["timeline"]["points"] * 4)]
    thinned = screenshots.test_facts(long, None, "")["timeline"]
    assert len(thinned["rows"]) <= screenshots.MAX_TIMELINE and thinned["bucket_s"] == 180


# ---------------------------------------------------------------------------
# The AI reading
# ---------------------------------------------------------------------------

def test_reading_is_mapped_cached_and_goes_stale(run, monkeypatch):
    doc = stepped_doc()
    ctx = {"region": "us-east-1", "notes": "Two app servers."}
    a = screenshots.add(run, "cpu.png", png(), "CPU – app servers")
    b = screenshots.add(run, "mem.png", png(30, 30, (0, 90, 0)))
    fake = FakeVision()
    monkeypatch.setattr(registry, "provider_for_task", lambda task, settings, override=None: fake)
    seen = []

    got = screenshots.analyse(doc, run, ctx, settings={}, tz_name="Asia/Kolkata",
                              progress=lambda done, total, message: seen.append((done, total, message)))
    assert not got["reused"] and not got["stale"] and got["provider"] == "fake/eye-1" and not got["local"]
    assert got["failed"] == 0 and fake.warmed == 1
    shots = got["content"]["screenshots"]
    assert [s["id"] for s in shots] == [a["id"], b["id"]]                          # by image number, in upload order
    assert shots[0]["title"] == "CPU, app servers" and shots[0]["covers_test"] == "unclear"   # unknown value
    assert shots[0]["observations"] == ["CPU rose with each load step to about 60 percent.", "It fell when the test ended."]
    assert shots[1]["covers_test"] == "yes" and shots[1]["time_axis"] == "13:30 to 15:10"
    assert got["content"]["observations"] == SUMMARY["observations"]              # from the summary step
    assert seen[-1] == (2, 2, "Writing the overall observations…") and seen[0][0] == 0

    [sent] = fake.batches()                                                        # two screenshots: one request
    assert [im.media_type for im in sent["images"]] == ["image/png", "image/png"]
    assert sent["images"][0].name == "CPU – app servers" and sent["images"][1].name == "mem.png"
    assert 'Image 1: caption "CPU – app servers"' in sent["prompt"] and "Image 2: no caption (file mem.png" in sent["prompt"]
    assert "Two app servers." in sent["prompt"] and '"local_time_zone":"Asia/Kolkata (UTC+05:30)"' in sent["prompt"]
    assert sent["schema"] == screenshots.SCHEMA and sent["max_tokens"] == 6000
    summary = fake.calls[-1]
    assert summary["images"] is None and summary["schema"] == screenshots.SUMMARY_SCHEMA
    assert '"title":"Memory used, app-server-1"' in summary["prompt"] and '"timeline"' not in summary["prompt"]

    again = screenshots.analyse(doc, run, ctx, settings={})
    assert again["reused"] and len(fake.calls) == 2
    assert screenshots.state(run, doc, ctx)["analysis"]["stale"] is False

    screenshots.update(run, b["id"], "Memory, app-server-1")                       # a caption changes what is read …
    assert screenshots.current(run, doc, ctx)["stale"] is True
    assert screenshots.current(run, doc, {**ctx, "notes": "Three servers."})["stale"] is True
    fresh = screenshots.analyse(doc, run, ctx, settings={})
    assert not fresh["reused"] and not fresh["stale"]
    assert [im.name for im in fake.batches()[-1]["images"]] == ["Memory, app-server-1"]   # … and only that one is read again
    everything = screenshots.analyse(doc, run, ctx, settings={}, fresh=True)
    assert len(fake.batches()[-1]["images"]) == 2 and not everything["reused"]

    screenshots.remove(run, a["id"])                                               # removed shots leave the reading
    assert [s["id"] for s in screenshots.current(run, doc, ctx)["content"]["screenshots"]] == [b["id"]]


def test_many_screenshots_are_read_in_batches_and_failures_are_retried(run, monkeypatch):
    doc = stepped_doc()
    names = [f"s{i}.png" for i in range(7)]
    items = [screenshots.add(run, n, png(20 + i, 20)) for i, n in enumerate(names)]
    shots = {n: shot_entry(f"Chart {n}") for n in names}
    fake = FakeVision(shots, fail={"s3.png"})                                      # the batch with s3 fails
    monkeypatch.setattr(registry, "provider_for_task", lambda task, settings, override=None: fake)

    got = screenshots.analyse(doc, run, None, settings={})
    batches = sorted(len(c["images"]) for c in fake.batches())
    # 7 = 3 + 3 + 1; the batch with s3 fails, so its three are read again one at a time
    assert batches == [1, 1, 1, 1, 3, 3]
    assert got["failed"] == 1 and not got["stale"]
    by = {x["id"]: x for x in got["content"]["screenshots"]}
    failed = [n for n, it in zip(names, items) if by[it["id"]].get("error")]
    assert failed == ["s3.png"] and "did not answer" in by[items[3]["id"]]["error"]
    assert by[items[4]["id"]]["title"] == "Chart s4.png"                             # its batch mates are kept
    assert got["content"]["observations"] == SUMMARY["observations"]

    fake.fail.clear()
    calls = len(fake.batches())
    retried = screenshots.analyse(doc, run, None, settings={})                    # Retry: only the failed one
    assert [len(c["images"]) for c in fake.batches()[calls:]] == [1] and retried["failed"] == 0
    assert [x["title"] for x in retried["content"]["screenshots"]] == [f"Chart {n}" for n in names]
    assert screenshots.analyse(doc, run, None, settings={})["reused"]


def test_summary_failure_keeps_the_readings(run, monkeypatch):
    screenshots.add(run, "cpu.png", png())

    class NoSummary(FakeVision):
        def complete(self, system, prompt, **kw):
            if not kw.get("images"):
                raise LLMError("quota exceeded")
            return super().complete(system, prompt, **kw)
    fake = NoSummary()
    monkeypatch.setattr(registry, "provider_for_task", lambda task, settings, override=None: fake)
    got = screenshots.analyse(stepped_doc(), run, None, settings={})
    assert got["content"]["screenshots"][0]["title"] == "CPU, app servers" and got["content"]["observations"] == []
    assert "could not be written (quota exceeded)" in got["content"]["caveats"][0]


def test_reading_needs_screenshots_and_a_vision_model(run, monkeypatch):
    with pytest.raises(screenshots.ScreenshotError, match="at least one"):
        screenshots.analyse(stepped_doc(), run, None, settings={})

    def no_key(task, settings, override=None):
        assert task == "screenshots"
        raise LLMConfigError("No Gemini API key")
    monkeypatch.setattr(registry, "provider_for_task", no_key)
    screenshots.add(run, "cpu.png", png())
    with pytest.raises(LLMConfigError):
        screenshots.analyse(stepped_doc(), run, None, settings={})


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def test_reports_carry_the_screenshots(run, monkeypatch):
    doc = stepped_doc()
    ctx = highlights.clean_context({"region": "us-east-1"})
    screenshots.add(run, "cpu.png", png(2400, 900), "CPU – app servers")
    screenshots.add(run, "mem.png", png())

    unread = screenshots.report_section(run, doc, ctx, embed=True)
    tech = render(doc, from_rules(doc, "tech"), "tech", "html", None, unread)
    assert "Server metrics from screenshots" in tech and tech.count('<img src="data:image/') == 2
    assert "have not been read by the AI" in tech
    exec_html = render(doc, from_rules(doc, "exec"), "exec", "html", None, unread)
    assert "Server metrics from screenshots" not in exec_html                     # nothing to say without a reading
    [first, _] = unread["pictures"]
    with PIL.open(io.BytesIO(base64.b64decode(first["src"].split(",", 1)[1]))) as im:
        assert max(im.size) == screenshots.REPORT_EDGE                              # scaled for the page

    fake = FakeVision()
    monkeypatch.setattr(registry, "provider_for_task", lambda task, settings, override=None: fake)
    screenshots.analyse(doc, run, ctx, settings={})
    read = screenshots.report_section(run, doc, ctx, embed=False, link_from=run / "web")
    md = render(doc, from_rules(doc, "tech"), "tech", "md", None, read)
    assert "## Server metrics from screenshots" in md and "- Both servers kept CPU headroom at 500 users." in md
    assert f"](../screenshots/{screenshots.list_items(run)[0]['file']})" in md
    assert "Chart shows 13:30 to 15:10, Asia/Kolkata · test 13:39 to 14:58" in md and "Read by fake/eye-1" in md
    exec_md = render(doc, from_rules(doc, "exec"), "exec", "md", None, read)
    assert "Both servers kept CPU headroom" in exec_md and "![" not in exec_md     # summary only, no pictures

    screenshots.update(run, screenshots.list_items(run)[1]["id"], "new caption")
    stale = screenshots.report_section(run, doc, ctx, embed=False)
    assert stale["stale"] and not stale["analysed"] and stale["observations"] == []
    assert "out of date" in render(doc, from_rules(doc, "tech"), "tech", "md", None, stale)


# ---------------------------------------------------------------------------
# Providers send the pictures
# ---------------------------------------------------------------------------

PICS = [Image(png(), "image/png", "CPU"), Image(jpeg(), "image/jpeg")]


def test_claude_and_openai_put_labelled_pictures_before_the_prompt(monkeypatch):
    from projectstack.llm.providers.claude import ClaudeProvider
    from projectstack.llm.providers.openai_compat import OpenAIProvider
    seen = {}

    class Resp:
        stop_reason, model = "end_turn", "claude-x"
        content = [type("B", (), {"type": "text", "text": "{}"})()]
        usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()

    claude = ClaudeProvider({"anthropic_api_key": "test-key"})
    monkeypatch.setattr(claude, "_create", lambda **kw: seen.setdefault("claude", kw) and Resp())
    claude.complete("sys", "read them", images=PICS)
    blocks = seen["claude"]["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["text", "image", "text", "image", "text"]
    assert blocks[0]["text"] == "Image 1 (CPU)" and blocks[2]["text"] == "Image 2"
    assert blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": PICS[0].b64}
    assert blocks[4] == {"type": "text", "text": "read them"}

    class Choice:
        finish_reason = "stop"
        message = type("M", (), {"content": "{}", "refusal": None})()

    class OResp:
        model, choices = "gpt-x", [Choice()]
        usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1})()

    oa = OpenAIProvider({"openai_api_key": "test-key"})
    monkeypatch.setattr(oa, "_create", lambda **kw: seen.setdefault("openai", kw) and OResp())
    oa.complete("sys", "read them", images=PICS)
    parts = seen["openai"]["messages"][1]["content"]
    assert parts[1] == {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PICS[0].b64}"}}
    assert parts[3]["image_url"]["url"].startswith("data:image/jpeg;base64,") and parts[4]["text"] == "read them"


def test_claude_text_only_prompt_stays_a_string(monkeypatch):
    from projectstack.llm.providers.claude import ClaudeProvider
    seen = []

    class Resp:
        stop_reason, model = "end_turn", "claude-x"
        content = [type("B", (), {"type": "text", "text": "ok"})()]
        usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()

    claude = ClaudeProvider({"anthropic_api_key": "test-key"})
    monkeypatch.setattr(claude, "_create", lambda **kw: seen.append(kw) or Resp())
    claude.complete("sys", "just text")
    assert seen[0]["messages"] == [{"role": "user", "content": "just text"}]


class FakeHTTP:
    def __init__(self, status=200, data=None):
        self.status_code, self.data = status, data or {}
        self.ok = status < 400
        self.text = json.dumps(self.data)

    def json(self):
        return self.data


def test_gemini_streams_pictures_and_skips_a_silent_model(monkeypatch):
    import requests
    from projectstack.correlator import llm_router
    from projectstack.llm.providers.local import GeminiProvider
    bodies = []

    class Stream(FakeHTTP):
        def iter_lines(self, decode_unicode=False):
            yield 'data: {"candidates": [{"content": {"parts": [{"text": "{\\"a\\": "}]}}]}'
            yield ""
            yield 'data: {"candidates": [{"content": {"parts": [{"text": "1}"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 9}}'

        def close(self):
            pass

    def post(url, headers=None, json=None, timeout=None, stream=False):
        bodies.append((url, json, stream, timeout))
        if "gemini-hung" in url:
            raise requests.ReadTimeout("no bytes")
        return Stream(200) if stream else FakeHTTP(200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(llm_router, "_gemini_thinking_ok", {"gemini-test": None, "gemini-hung": None})
    monkeypatch.setattr(llm_router, "_gemini_cooldown", {})
    monkeypatch.setattr(llm_router, "_gemini_models", lambda key: ["gemini-hung", "gemini-test"])

    text = GeminiProvider({"gemini_api_key": "test-key"}).complete("sys", "read them", images=PICS, json_schema={"type": "object"})
    assert text.text == '{"a": 1}' and text.model == "gemini-test"
    (hung_url, _, _, hung_timeout), (url, body, stream, _) = bodies
    assert "gemini-hung:streamGenerateContent?alt=sse" in hung_url and hung_timeout[1] <= llm_router.STREAM_IDLE_S
    assert stream and "gemini-test:streamGenerateContent?alt=sse" in url
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "Image 1 (CPU)"} and parts[1] == {"inlineData": {"mimeType": "image/png", "data": PICS[0].b64}}
    assert parts[3]["inlineData"]["mimeType"] == "image/jpeg" and parts[-1] == {"text": "read them"}
    assert ("test-key", "gemini-hung") in llm_router._gemini_cooldown                # skipped for a while
    GeminiProvider({"gemini_api_key": "test-key"}).complete("sys", "again", images=PICS)
    assert "gemini-hung" not in bodies[-1][0]

    bodies.clear()
    GeminiProvider({"gemini_api_key": "test-key"}, "gemini-test").complete("sys", "no pictures")
    assert ":generateContent" in bodies[0][0] and bodies[0][2] is False                # text stays a plain request


def test_gemini_probe_marks_models_that_do_not_answer(monkeypatch):
    import requests
    from projectstack.correlator import llm_router
    monkeypatch.setattr(llm_router, "_gemini_cooldown", {})
    monkeypatch.setattr(llm_router, "_gemini_models", lambda key: ["busy", "quota", "hung", "fine"])

    def post(url, headers=None, json=None, timeout=None):
        if "/hung:" in url:
            raise requests.ReadTimeout("no bytes")
        return FakeHTTP(503) if "/busy:" in url else FakeHTTP(429, {"error": "quota exceeded"}) if "/quota:" in url \
            else FakeHTTP(200, {"candidates": []})
    monkeypatch.setattr(requests, "post", post)
    assert llm_router._gemini_probe("k") == ["fine"]
    assert {m for _, m in llm_router._gemini_cooldown} == {"busy", "quota", "hung"}


def test_ollama_checks_the_model_can_see(monkeypatch):
    import requests
    from projectstack.llm.providers.local import OllamaProvider
    calls = []

    def post(url, json=None, timeout=None):
        calls.append((url, json))
        if url.endswith("/api/show"):
            return FakeHTTP(200, {"capabilities": ["completion", "vision"] if json["model"] == "gemma4" else ["completion"]})
        return FakeHTTP(200, {"message": {"content": "{}"}, "done_reason": "stop"})
    monkeypatch.setattr(requests, "post", post)

    with pytest.raises(LLMConfigError, match="cannot read images"):
        OllamaProvider({"ollama_model": "mistral"}).complete("sys", "read them", images=PICS)
    OllamaProvider({"ollama_model": "gemma4"}).complete("sys", "read them", images=PICS)
    url, body = calls[-1]
    user = body["messages"][-1]
    assert url.endswith("/api/chat") and user["images"] == [PICS[0].b64, PICS[1].b64]
    assert user["content"].startswith("Image 1 (CPU) is picture 1 of 2.") and user["content"].endswith("read them")
    assert 8192 <= body["options"]["num_ctx"] <= 32768

    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: FakeHTTP(
        200, {"capabilities": ["vision"]}) if url.endswith("/api/show") else FakeHTTP(
        500, {"error": "model requires more system memory (9.8 GiB) than is available (6.3 GiB)"}))
    with pytest.raises(LLMConfigError, match="more system memory .*close other programs"):
        OllamaProvider({"ollama_model": "gemma4"}).complete("sys", "read them", images=PICS)


# ---------------------------------------------------------------------------
# Which providers can read screenshots
# ---------------------------------------------------------------------------

def test_vision_choices(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(registry, "ollama_vision_models", lambda settings: ["llava:latest", "gemma4:latest"])
    got = registry.vision_choices({"gemini_api_key": "k", "groq_api_key": "k", "ollama_model": "gemma4"})
    assert [c["spec"] for c in got["choices"]] == ["gemini", "ollama/gemma4:latest", "ollama/llava:latest"]
    assert got["choices"][1] == {"spec": "ollama/gemma4:latest", "label": "Ollama gemma4", "local": True, "where": "this machine"}
    assert got["default"] == "gemini"                                    # what reports use, when it can see

    local_only = registry.vision_choices({"ai_provider": "ollama", "ollama_model": "gemma4"})
    assert local_only["default"] == "ollama/gemma4:latest"
    chosen = registry.vision_choices({"gemini_api_key": "k", "llm_tasks": json.dumps({"screenshots": "ollama/qwen2.5vl"})})
    assert chosen["default"] == "ollama/qwen2.5vl" and chosen["choices"][0]["spec"] == "ollama/qwen2.5vl"
    monkeypatch.setattr(registry, "ollama_vision_models", lambda settings: [])
    assert registry.vision_choices({}) == {"choices": [], "default": None}
    assert "screenshots" in registry.TASKS


# ---------------------------------------------------------------------------
# Lines read off the screenshots, compared with the load test
# ---------------------------------------------------------------------------

def test_lines_read_off_screenshots_are_compared_with_the_load(run, monkeypatch):
    doc = stepped_doc()
    times = screenshots.read_at(doc)
    assert times == sorted(times) and len(times) == 27 and times[:2] == [0.0, 180.0]   # every 3rd minute of 80
    users = {p["t_offset_s"]: p["users"] for p in doc["timeline"]["points"]}
    cpu = [round(5 + users[t] * 0.11, 1) for t in times]
    shots = {
        "cpu.png": shot_entry("CPU", series=[
            {"name": "CPU · app-server-1", "unit": "percent", "values": cpu[:-2] + ["61%", "n/a"]},
            {"name": "too few", "unit": "%", "values": [1, 2]}], observations=["CPU followed the ramp."]),
        "old.png": shot_entry("CPU yesterday", time_axis="09:00 to 10:00", test_on_chart="after the chart ends",
                              covers_test="no", clock="utc", series=[{"name": "CPU · other", "unit": "%", "values": cpu}],
                              observations=[]),
    }
    a = screenshots.add(run, "cpu.png", png(), "CPU")
    screenshots.add(run, "old.png", png(30, 30))
    fake = FakeVision(shots, summary={"observations": ["The app server had headroom."], "caveats": []})
    monkeypatch.setattr(registry, "provider_for_task", lambda task, settings, override=None: fake)

    got = screenshots.analyse(doc, run, {"region": "us-east-1"}, settings={}, tz_name="Asia/Kolkata")
    prompt = fake.batches()[0]["prompt"]
    assert '"read_at":{"note":' in prompt and '"columns":["i","utc","local"],"rows":[[0,"08:09","13:39"],[1,"08:12","13:42"]' in prompt
    assert "series" in fake.batches()[0]["schema"]["properties"]["screenshots"]["items"]["properties"]

    shot = got["content"]["screenshots"][0]
    assert shot["clock"] == "local" and [s["name"] for s in shot["series"]] == ["CPU · app-server-1"]   # 2 values: dropped
    assert shot["series"][0]["values"][-2:] == [61.0, None] and len(shot["series"][0]["values"]) == 27
    assert got["content"]["read_at_s"] == times
    rel = got["content"]["relation"]
    assert rel["source"] == "screenshots" and [r["name"] for r in rel["rows"]] == ["CPU · app-server-1"]  # not the "no" chart
    row = rel["rows"][0]
    assert row["unit"] == "%" and row["load"] == "strong" and row["shot"] == a["id"] and row["points"] == 26
    assert any("followed the load" in o for o in rel["observations"])

    section = screenshots.report_section(run, doc, {"region": "us-east-1"}, embed=False)
    md = render(doc, from_rules(doc, "tech"), "tech", "md", None, section)
    assert "### Server metrics vs the load test" in md and "| CPU · app-server-1 | strong · " in md
    assert "read off the screenshots by the AI, so they are approximate" in md
    html = render(doc, from_rules(doc, "tech"), "tech", "html", None, screenshots.report_section(
        run, doc, {"region": "us-east-1"}, embed=True))
    assert "Server metrics vs the load test" in html and "<th>With p95 response time</th>" in html
    exec_md = render(doc, from_rules(doc, "exec"), "exec", "md", None, section)
    assert "followed the load" in exec_md and "| Server metric |" not in exec_md     # the summary, not the table

    screenshots.remove(run, a["id"])                                                # its rows go with it
    assert screenshots.current(run, doc, {"region": "us-east-1"})["content"]["relation"] is None
