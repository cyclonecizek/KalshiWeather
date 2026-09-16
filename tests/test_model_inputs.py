from pipeline.model_inputs import describe_inputs


def config():
    families={'a':{'weight':.6,'members':['A','B']},'b':{'weight':.4,'members':['C']}}
    return {'families':families,'temperature':{'families':families,'sources':{'meteoblue':{'publish_values':False}}}}


def test_missing_sources_renormalize_without_inventing_forecasts():
    rows=describe_inputs(config(),'rain',{'models':{'A':.2,'B':.6}})
    assert [r['weight'] for r in rows]==[.5,.5,0]
    assert rows[2]['value'] is None
    assert not rows[2]['included']
    assert sum(r['weight']*r['value'] for r in rows if r['included'])==.4


def test_temperature_shares_are_per_model_not_member_count():
    d={'diagnostics':{'A':{'median':80,'n':51,'p10':78,'p90':82},'C':{'value':85,'type':'point'}}}
    rows=describe_inputs(config(),'temperature',d)
    assert [r['weight'] for r in rows]==[.6,0,.4]
    assert rows[0]['p10']==78
    assert rows[2]['value']==85
