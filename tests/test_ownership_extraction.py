"""Public extraction regressions with only Bilibili HTTP and time mocked."""
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote_from_bytes
from unittest.mock import patch

import httpx
import pytest

from biliscribe.api import BiliClient, BiliError
from biliscribe.service import extract_one

BVID = "BV1aDn968E2A"
AID, CID = 117505221724658, 43061546452
OPAQUE = "e8edb3c0dc6f558b5b433ba24ec806be"


def encoded_track_url(resource=OPAQUE, key_index=1):
    # Public player encoding samples, deliberately independent of product constants.
    pairs = [
        (r'nP](wOFRvU.+<fjS{jn-!$D|Dz&",zT`',
         r'=CFxYRn{.y|uVyO$uh&sikph?N.ilF/`bilibili'),
        (r'Bn"q~|albg@]Go~ACgyDvKnd+)_D}^&J?',
         r"Cu~L!xs~f^&r@'vh=q]q{eeng*sEg^kp#Jbilibili"),
    ]
    prefix, key = pairs[key_index]
    plain = (prefix + '/bfs/ai_subtitle/prod/' + resource).encode()
    cipher = bytes(byte ^ ord(key[index % len(key)]) for index, byte in enumerate(plain))
    return '//subtitle.bilibili.com/' + quote_from_bytes(cipher, safe='') + '?auth_key=mock-signature'


def protobuf_tracks(tracks):
    def varint(value):
        result = bytearray()
        while value > 127:
            result.append((value & 127) | 128)
            value >>= 7
        result.append(value)
        return bytes(result)

    def field(number, value):
        data = value.encode() if isinstance(value, str) else value
        return varint(number * 8 + 2) + varint(len(data)) + data

    return field(1, b"".join(field(3,
        field(3, track["lan"]) + field(5, track["subtitle_url"])) for track in tracks))


@contextmanager
def extraction_client(tmp_path, *, legacy=(), modern=b"\x0a\x00", modern_status=200,
                      legacy_code=0, body=None, body_status=200):
    requests = []

    def respond(request):
        requests.append(request)
        path = request.url.path
        if path == "/x/web-interface/view":
            return httpx.Response(200, json={"code": 0, "data": {
                "bvid": BVID, "aid": AID, "cid": CID, "title": "AI 观众提问",
                "owner": {"name": "UP", "mid": 123}, "duration": 824,
                "pages": [{"page": 1, "cid": CID, "part": "正片", "duration": 824}]}})
        if path == f"/video/{BVID}":
            return httpx.Response(200, text="<html></html>")
        if path == "/x/v2/subtitle/web/view":
            assert request.url.params['pid'] == str(AID)
            assert request.url.params['oid'] == str(CID)
            return httpx.Response(modern_status, content=modern)
        if path == "/x/player/v2":
            return httpx.Response(200, json={"code": legacy_code,
                "message": "mock API failure",
                "data": {"subtitle": {"subtitles": list(legacy)}}})
        if request.url.host == "aisubtitle.hdslb.com":
            return httpx.Response(body_status, json={"body": body or []})
        raise AssertionError(f"Unexpected external request: {request.url.host}{path}")

    constructor = httpx.Client
    def client_factory(**kwargs):
        return constructor(**kwargs, transport=httpx.MockTransport(respond))

    with patch("biliscribe.api.CACHE_DIR", tmp_path / "cache"), \
            patch("biliscribe.api.httpx.Client", side_effect=client_factory), \
            patch("time.sleep", return_value=None):
        with BiliClient(setup_fingerprint=False) as client:
            yield client, requests


def extract(client, tmp_path, **kwargs):
    return extract_one(client, f"https://www.bilibili.com/video/{BVID}/",
                       ["txt"], "all", ["zh"], tmp_path / "exports",
                       lambda _: None, **kwargs)


def test_legacy_opaque_track_is_unverified_and_never_transcribed(tmp_path):
    track = {"lan": "ai-zh", "subtitle_url":
             f"//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/{OPAQUE}"}
    with extraction_client(tmp_path, legacy=[track]) as (client, requests):
        with pytest.raises(BiliError) as raised:
            extract(client, tmp_path, asr_engine=object(), audio_source=object())
    assert raised.value.reason == "ownership_unverified"
    assert "无法验证" in raised.value.message
    assert "其他视频" not in raised.value.message
    assert not list((tmp_path / "exports").glob("*"))
    assert all(request.url.host != "aisubtitle.hdslb.com" for request in requests)


def test_modern_identity_conflict_survives_empty_legacy_response(tmp_path):
    track = {"lan": "ai-zh", "subtitle_url":
             "//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11337329541521726472939880ffffffffffffffffffffffffffffffff"}
    with extraction_client(tmp_path, modern=protobuf_tracks([track])) as (client, requests):
        with pytest.raises(BiliError) as raised:
            extract(client, tmp_path, asr_engine=object(), audio_source=object())
    assert raised.value.reason == "ownership_conflict"
    assert not list((tmp_path / "exports").glob("*"))
    assert all(request.url.host != "aisubtitle.hdslb.com" for request in requests)


@pytest.mark.parametrize("settings, reason", [
    ({"modern": b"\x0a\x80"}, "subtitle_parse_error"),
    ({"modern_status": 503}, "network_error"),
    ({"modern_status": 401}, "auth_required"),
    ({"modern_status": 429}, "rate_limited"),
    ({"legacy_code": -101}, "auth_required"),
    ({"legacy_code": -412}, "rate_limited"),
])
def test_api_failures_are_errors_without_asr(tmp_path, settings, reason):
    with extraction_client(tmp_path, **settings) as (client, requests):
        with pytest.raises(BiliError) as raised:
            extract(client, tmp_path, asr_engine=object(), audio_source=object())
    assert raised.value.reason == reason
    assert not list((tmp_path / "exports").glob("*"))
    assert all(request.url.host != "aisubtitle.hdslb.com" for request in requests)


def test_empty_list_is_distinct_from_unverified_track(tmp_path):
    with extraction_client(tmp_path) as (client, _):
        result = extract(client, tmp_path)
    assert result["status"] == "no_subtitle"
    assert result["reason"] == "subtitle_absent"
    assert result["files"] == []


@pytest.mark.parametrize("lan, reason", [
    ("ai-en", "language_unavailable"), ("ai-zh", "subtitle_body_empty"),
])
def test_other_language_and_empty_body_have_different_reasons(tmp_path, lan, reason):
    track = {"lan": lan, "subtitle_url":
             f"//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/{AID}{CID}fixture"}
    with extraction_client(tmp_path, modern=protobuf_tracks([track])) as (client, _):
        result = extract(client, tmp_path)
    assert result["status"] == "no_subtitle"
    assert result["reason"] == reason
    assert result["files"] == []


def test_body_network_failure_is_not_reported_as_empty_subtitle(tmp_path):
    track = {"lan": "ai-zh", "subtitle_url":
             f"//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/{AID}{CID}fixture"}
    with extraction_client(tmp_path, modern=protobuf_tracks([track]), body_status=503) as (client, _):
        with pytest.raises(BiliError) as raised:
            extract(client, tmp_path, asr_engine=object(), audio_source=object())
    assert raised.value.reason == "network_error"
    assert not list((tmp_path / "exports").glob("*"))


@pytest.mark.parametrize('key_index', [0, 1])
@pytest.mark.parametrize('resource', [OPAQUE, '01234567890123456789012345678901'])
def test_modern_encoded_opaque_subtitle_exports_without_legacy(tmp_path, key_index, resource):
    track = {'lan': 'ai-zh', 'subtitle_url': encoded_track_url(resource, key_index)}
    with extraction_client(tmp_path, modern=protobuf_tracks([track]),
                           body=[{'from': 1.25, 'to': 2.5, 'content': '固定字幕样本'}]) as (client, requests):
        result = extract(client, tmp_path)
    assert result['status'] == 'ok'
    assert result['tracks'] == 1
    assert len(result['files']) == 1
    assert Path(result['files'][0]).read_text(encoding='utf-8') == '固定字幕样本'
    assert not any(request.url.path == '/x/player/v2' for request in requests)
    assert any(request.url.path == '/bfs/ai_subtitle/prod/' + resource for request in requests)


@pytest.mark.parametrize('source', ['modern', 'legacy'])
def test_mixed_tracks_deliver_only_the_matching_resource(tmp_path, source):
    good = {'lan': 'ai-zh', 'subtitle_url':
            f'//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/{AID}{CID}fixture'}
    bad = {'lan': 'ai-zh', 'subtitle_url':
           '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11337329541521726472939880ffffffffffffffffffffffffffffffff'}
    settings = {'modern': protobuf_tracks([good, bad])} if source == 'modern' else {'legacy': [good, bad]}
    with extraction_client(tmp_path, **settings,
                           body=[{'from': 1, 'to': 2, 'content': '仅导出匹配轨道'}]) as (client, requests):
        result = extract(client, tmp_path)
    assert result['status'] == 'ok'
    assert result['tracks'] == 1
    assert Path(result['files'][0]).read_text(encoding='utf-8') == '仅导出匹配轨道'
    assert all('11337329541521726472939880' not in request.url.path for request in requests)


@pytest.mark.parametrize('url', [
    '//subtitle.bilibili.com/%01opaque',
    'https://untrusted.example/bfs/ai_subtitle/prod/' + OPAQUE,
    'https://aisubtitle.hdslb.com/unknown/prod/' + OPAQUE,
])
def test_unknown_modern_resource_is_not_authorized_by_opaque_shape(tmp_path, url):
    with extraction_client(tmp_path, modern=protobuf_tracks([
            {'lan': 'ai-zh', 'subtitle_url': url}])) as (client, requests):
        with pytest.raises(BiliError) as raised:
            extract(client, tmp_path)
    assert raised.value.reason == 'ownership_unverified'
    assert not any(request.url.host == 'aisubtitle.hdslb.com' for request in requests)


def test_legacy_resource_cannot_claim_modern_approval(tmp_path):
    track = {'lan': 'ai-zh', 'subtitle_url': encoded_track_url(),
             'source': 'modern', '_owner_key': f'{BVID}:{AID}:{CID}',
             'policy_version': 1, 'author_mid': 123}
    with extraction_client(tmp_path, legacy=[track]) as (client, requests):
        with pytest.raises(BiliError) as raised:
            extract(client, tmp_path)
    assert raised.value.reason == 'ownership_unverified'
    assert not any(request.url.host == 'aisubtitle.hdslb.com' for request in requests)


def test_legacy_author_conflict_is_not_overridden_by_resource_prefix(tmp_path):
    track = {'lan': 'ai-zh', 'author_mid': 999, 'subtitle_url':
             f'//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/{AID}{CID}fixture'}
    with extraction_client(tmp_path, legacy=[track]) as (client, requests):
        with pytest.raises(BiliError) as raised:
            extract(client, tmp_path)
    assert raised.value.reason == 'ownership_conflict'
    assert not any(request.url.host == 'aisubtitle.hdslb.com' for request in requests)


def test_repeated_opaque_extraction_revalidates_before_durable_cache_support(tmp_path):
    track = {'lan': 'ai-zh', 'subtitle_url': encoded_track_url()}
    with extraction_client(tmp_path, modern=protobuf_tracks([track]),
                           body=[{'from': 1, 'to': 2, 'content': '重复获取来源'}]) as (client, requests):
        first = extract(client, tmp_path)
        second = extract(client, tmp_path)
    assert first['status'] == second['status'] == 'ok'
    assert sum(request.url.path == '/x/v2/subtitle/web/view' for request in requests) == 2
    assert sum(request.url.host == 'aisubtitle.hdslb.com' for request in requests) == 2
