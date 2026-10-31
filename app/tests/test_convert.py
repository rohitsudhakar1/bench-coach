import os
from pathlib import Path
import httpx
import pytest
from protocols.convert import convert_text, rule_protocol, validate_protocol


def test_evidence_and_waits():
    result = rule_protocol('1. Attach a tip. 2. Draw 10 uL from tube A. 3. Dispense into tube B. 4. Eject the tip. 5. Incubate for 2 minutes.')
    assert [s['expect'][0]['kind'] if s['expect'] else None for s in result['steps']] == ['tip_on','draw','dispense','tip_off',None]
    assert result['steps'][1]['expect'][0]['object'] == 'tube A'
    assert result['steps'][2]['expect'][0]['object'] == 'tube B'
    assert result['steps'][-1]['min_seconds'] == 120
    assert rule_protocol('Centrifuge to collect liquid.')['steps'][0]['expect'] == []
    assert rule_protocol('Mix by pipetting up and down.')['steps'][0]['expect'][0]['kind'] == 'dispense'


def test_claude_and_fallback(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','test-key')
    protocol = rule_protocol('Attach a tip.')
    def reply(*args, **kwargs):
        assert kwargs['headers']['x-api-key'] == 'test-key'
        assert kwargs['json']['tools'][0]['input_schema']['properties']['steps']
        return httpx.Response(200,request=httpx.Request('POST','https://example.test'),json={
            'stop_reason':'tool_use','content':[{'type':'tool_use','name':'protocol','input':protocol}]})
    monkeypatch.setattr(httpx,'post',reply)
    assert convert_text('Attach a tip.')[1] == 'claude'
    monkeypatch.setattr(httpx,'post',lambda *a,**kw: httpx.Response(200,request=httpx.Request('POST','https://example.test'),json={'content':[]}))
    assert convert_text('Attach a tip.')[1] == 'rules-fallback'
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    assert convert_text('Attach a tip.')[1] == 'rules'
    for value in ['', ' ' * 3, 'x' * 60001]:
        with pytest.raises(ValueError):
            convert_text(value)


FIXTURES = sorted(Path(os.path.join(os.environ.get('BENCH_SAMPLES', ''), 'lsv_mv')).glob('*protocol.txt')) + sorted(Path(os.path.join(os.environ.get('BENCH_SAMPLES', ''), 'lsv_dji')).glob('*.txt'))


@pytest.mark.parametrize('path',FIXTURES,ids=lambda p:p.name)
def test_real_protocol_texts(path):
    text = path.read_text()
    document, method = convert_text(text,path.stem,use_llm=False)
    assert method == 'rules'
    assert validate_protocol(document) == document
    assert document['steps'] and all(s['text'] for s in document['steps'])
    if path.name == 'CP-414-445-protocol.txt':
        assert [s['expect'][0]['kind'] for s in document['steps']] == ['tip_on','draw','dispense','draw']
        assert document['steps'][1]['expect'][0]['object'] == 'trypsin'
