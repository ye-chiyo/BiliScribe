"""Public extraction regressions with only Bilibili HTTP and time mocked."""
import json
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote_from_bytes
from unittest.mock import patch

import httpx
import pytest

from biliscribe.api import BiliClient, BiliError, extract_urls
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
                      legacy_code=0, legacy_status=200, legacy_raw=None,
                      body=None, body_status=200, info=None):
    requests = []

    def respond(request):
        requests.append(request)
        path = request.url.path
        if path == "/x/web-interface/view":
            default_info = {
                "bvid": BVID, "aid": AID, "cid": CID, "title": "AI 观众提问",
                "owner": {"name": "UP", "mid": 123}, "duration": 824,
                "pages": [{"page": 1, "cid": CID, "part": "正片", "duration": 824}]}
            return httpx.Response(200, json={"code": 0, "data":
                info(request) if callable(info) else info or default_info})
        if request.url.host == "www.bilibili.com":
            return httpx.Response(200, text="<html></html>")
        if path == "/x/v2/subtitle/web/view":
            if info is None:
                assert request.url.params['pid'] == str(AID)
                assert request.url.params['oid'] == str(CID)
            return httpx.Response(modern_status, content=modern(request) if callable(modern) else modern)
        if path == "/x/player/v2":
            if legacy_raw is not None:
                return httpx.Response(legacy_status, text=legacy_raw)
            return httpx.Response(legacy_status, json={"code": legacy_code,
                "message": "mock API failure",
                "data": {"subtitle": {"subtitles": list(legacy(request) if callable(legacy) else legacy)}}})
        if request.url.host == "aisubtitle.hdslb.com":
            return httpx.Response(body_status(request) if callable(body_status) else body_status, json={"body": body(request) if callable(body) else body or []})
        raise AssertionError(f"Unexpected external request: {request.url.host}{path}")

    constructor = httpx.Client
    def client_factory(**kwargs):
        return constructor(**kwargs, transport=httpx.MockTransport(respond))

    with patch("biliscribe.api.CACHE_DIR", tmp_path / "cache"), \
            patch("biliscribe.api.httpx.Client", side_effect=client_factory), \
            patch("time.sleep", return_value=None):
        with BiliClient(setup_fingerprint=False) as client:
            yield client, requests


def extract(client, tmp_path, *, url=None, fmts=None, langs=None, pages_spec="all", **kwargs):
    return extract_one(client, url or f"https://www.bilibili.com/video/{BVID}/",
                       fmts or ["txt"], pages_spec, langs or ["zh"], tmp_path / "exports",
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


def test_repeated_opaque_extraction_reuses_validated_cache(tmp_path):
    track = {'lan': 'ai-zh', 'subtitle_url': encoded_track_url()}
    with extraction_client(tmp_path, modern=protobuf_tracks([track]),
                           body=[{'from': 1, 'to': 2, 'content': '重复获取来源'}]) as (client, requests):
        first = extract(client, tmp_path)
        second = extract(client, tmp_path)
    assert first['status'] == second['status'] == 'ok'
    assert sum(request.url.path == '/x/v2/subtitle/web/view' for request in requests) == 1
    assert sum(request.url.host == 'aisubtitle.hdslb.com' for request in requests) == 1


@pytest.mark.parametrize('damage', ['bare-list', 'version', 'scope', 'proof-scope', 'source', 'track', 'future', 'expired', 'malformed'])
def test_invalid_cached_evidence_requeries_instead_of_exporting(tmp_path, damage):
    track = {'lan': 'ai-zh', 'subtitle_url': encoded_track_url()}
    available = True
    with extraction_client(tmp_path, modern=lambda _: protobuf_tracks([track]) if available else b'\x0a\x00',
                           body=[{'from': 1, 'to': 2, 'content': 'valid initial'}]) as (client, requests):
        assert extract(client, tmp_path)['status'] == 'ok'
        cache_file = next((tmp_path / 'cache').glob('*_list.json'))
        data = json.loads(cache_file.read_text(encoding='utf-8'))
        if damage == 'bare-list': data = [track]
        elif damage == 'version': data['version'] = -1
        elif damage == 'scope': data['cid'] = CID + 1
        elif damage == 'proof-scope': data['entries'][0]['context'] = [BVID, AID, CID + 1]
        elif damage == 'source': data['entries'][0]['source'] = 'legacy'
        elif damage == 'track': data['entries'][0]['track']['subtitle_url'] = encoded_track_url('f' * 32)
        elif damage == 'future': data['entries'][0]['approved_at'] = time.time() + 3600
        elif damage == 'expired': data['entries'][0]['approved_at'] = time.time() - 90000
        elif damage == 'malformed': data['entries'] = [None]
        cache_file.write_text(json.dumps(data), encoding='utf-8')
        available = False
        result = extract(client, tmp_path)
    assert result['status'] == 'no_subtitle'
    assert result['files'] == []
    assert sum(request.url.path == '/x/v2/subtitle/web/view' for request in requests) == 2
    assert sum(request.url.host == 'aisubtitle.hdslb.com' for request in requests) == 1


@pytest.mark.parametrize('old_status', [200, 403])
def test_signature_or_empty_body_refreshes_metadata_and_exports(tmp_path, old_status):
    metadata_calls = 0
    def modern(_):
        nonlocal metadata_calls
        metadata_calls += 1
        url = encoded_track_url().replace('mock-signature', 'old' if metadata_calls == 1 else 'new')
        return protobuf_tracks([{'lan': 'ai-zh', 'subtitle_url': url}])
    with extraction_client(tmp_path, modern=modern,
            body_status=lambda request: old_status if request.url.params.get('auth_key') == 'old' else 200,
            body=lambda request: [] if request.url.params.get('auth_key') == 'old' else
                [{'from': 1, 'to': 2, 'content': 'refreshed signature'}]) as (client, requests):
        result = extract(client, tmp_path)
    assert result['status'] == 'ok'
    assert Path(result['files'][0]).read_text(encoding='utf-8') == 'refreshed signature'
    assert metadata_calls == 2


def test_same_resource_stays_isolated_across_videos_pages_and_all_formats(tmp_path):
    other = 'BV11gYd6UEPv'
    current_cid = CID
    def view(request):
        bvid = request.url.params.get('bvid', BVID)
        return {'bvid': bvid, 'aid': AID, 'cid': CID, 'title': 'format/test',
                'owner': {'name': 'UP', 'mid': 123}, 'duration': 10,
                'pages': [{'page': 1, 'cid': CID, 'part': 'one', 'duration': 10},
                          {'page': 2, 'cid': CID + 1, 'part': 'two', 'duration': 10}]}
    def modern(request):
        nonlocal current_cid
        current_cid = int(request.url.params['oid'])
        assert request.url.params['pid'] == str(AID)
        return protobuf_tracks([{'lan': 'ai-zh', 'subtitle_url': encoded_track_url()},
                                {'lan': 'ai-en', 'subtitle_url': encoded_track_url('f' * 32)}])
    def body(request):
        bvid = request.headers['referer'].rstrip('/').split('/')[-1]
        text = ('video two page one' if bvid == other else
                'video one page two' if current_cid == CID + 1 else 'video one page one')
        return [{'from': 1.9996, 'to': 3.0001, 'content': text}]
    formats = ['srt', 'vtt', 'txt', 'json', 'md']
    with extraction_client(tmp_path, info=view, modern=modern, body=body) as (client, requests):
        for url, expected in [
            (f'https://www.bilibili.com/video/{BVID}/?p=1', 'video one page one'),
            (f'https://www.bilibili.com/video/{BVID}/?p=2', 'video one page two'),
            (f'https://www.bilibili.com/video/{other}/?p=1', 'video two page one')]:
            result = extract(client, tmp_path, url=extract_urls(url)[0], fmts=formats)
            assert result['status'] == 'ok' and len(result['files']) == 5
            outputs = {Path(name).suffix: Path(name) for name in result['files']}
            assert outputs['.txt'].read_text(encoding='utf-8') == expected
            assert outputs['.srt'].read_bytes().startswith(b'\xef\xbb\xbf')
            assert outputs['.srt'].read_text(encoding='utf-8-sig') == f'1\n00:00:02,000 --> 00:00:03,000\n{expected}\n'
            assert outputs['.vtt'].read_text(encoding='utf-8') == f'WEBVTT\n\n00:00:02.000 --> 00:00:03.000\n{expected}\n'
            assert json.loads(outputs['.json'].read_text(encoding='utf-8'))['body'][0]['content'] == expected
            assert f'**[0:01]** {expected}' in outputs['.md'].read_text(encoding='utf-8')
        assert sum(request.url.host == 'aisubtitle.hdslb.com' for request in requests) == 3
        assert not any(request.url.path.endswith('/' + 'f' * 32) for request in requests)
        both = extract(client, tmp_path, url=f'https://www.bilibili.com/video/{other}/?p=1', langs=['all'])
        assert both['tracks'] == 2 and len(both['files']) == 2
        english = extract(client, tmp_path, url=f'https://www.bilibili.com/video/{other}/?p=1', langs=['en'])
        assert english['tracks'] == 1 and '.ai-en.' in english['files'][0]


def test_cached_opaque_evidence_survives_new_client_without_renewing_proof(tmp_path):
    track = {'lan': 'ai-zh', 'subtitle_url': encoded_track_url()}
    with extraction_client(tmp_path, modern=protobuf_tracks([track]),
                           body=[{'from': 1, 'to': 2, 'content': 'persisted sample'}]) as (client, _):
        assert extract(client, tmp_path)['status'] == 'ok'
    with extraction_client(tmp_path, modern_status=503) as (client, requests):
        result = extract(client, tmp_path)
    assert result['status'] == 'ok'
    assert Path(result['files'][0]).read_text(encoding='utf-8') == 'persisted sample'
    assert not any(request.url.path == '/x/v2/subtitle/web/view' or
                   request.url.host == 'aisubtitle.hdslb.com' for request in requests)


def test_legacy_transient_unverified_track_can_recover(tmp_path):
    responses = iter([
        [{'lan': 'ai-zh', 'subtitle_url': encoded_track_url()}],
        [{'lan': 'ai-zh', 'subtitle_url': f'//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/{AID}{CID}fixture'}],
    ])
    with extraction_client(tmp_path, legacy=lambda _: next(responses),
                           body=[{'from': 0, 'to': 1, 'content': '重试取得正确字幕'}]) as (client, _):
        result = extract(client, tmp_path)
    assert result['status'] == 'ok'
    assert Path(result['files'][0]).read_text(encoding='utf-8') == '重试取得正确字幕'


def test_modern_conflict_survives_legacy_http_failure(tmp_path):
    track = {'lan': 'ai-zh', 'subtitle_url':
             '//aisubtitle.hdslb.com/bfs/ai_subtitle/prod/11337329541521726472939880ffffffffffffffffffffffffffffffff'}
    with extraction_client(tmp_path, modern=protobuf_tracks([track]), legacy_status=503) as (client, requests):
        with pytest.raises(BiliError) as raised:
            extract(client, tmp_path)
    assert raised.value.reason == 'ownership_conflict'
    assert all(request.url.host != 'aisubtitle.hdslb.com' for request in requests)


@pytest.mark.parametrize('status, reason', [(401, 'auth_required'), (429, 'rate_limited')])
def test_body_http_failure_retains_specific_reason(tmp_path, status, reason):
    track = {'lan': 'ai-zh', 'subtitle_url': encoded_track_url()}
    with extraction_client(tmp_path, modern=protobuf_tracks([track]), body_status=status) as (client, _):
        with pytest.raises(BiliError) as raised:
            extract(client, tmp_path)
    assert raised.value.reason == reason
    assert not list((tmp_path / 'exports').glob('*'))


@pytest.mark.parametrize('status, raw, reason', [
    (401, '{}', 'auth_required'),
    (429, '{}', 'rate_limited'),
    (200, '{broken', 'subtitle_parse_error'),
    (200, '{}', 'subtitle_api_error'),
])
def test_legacy_http_and_payload_failures_are_not_absent_subtitles(tmp_path, status, raw, reason):
    with extraction_client(tmp_path, legacy_status=status, legacy_raw=raw) as (client, requests):
        with pytest.raises(BiliError) as raised:
            extract(client, tmp_path)
    assert raised.value.reason == reason
    assert not list((tmp_path / 'exports').glob('*'))
    assert all(request.url.host != 'aisubtitle.hdslb.com' for request in requests)
