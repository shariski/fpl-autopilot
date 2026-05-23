from src.decisions import bench


def _seed(db):
    db.execute("INSERT INTO gameweeks (id, finished) VALUES (30, 0)")
    for e in (13, 14, 15):
        db.execute("INSERT INTO players (id, web_name) VALUES (?, ?)", (e, f"P{e}"))
    db.commit()


def _picks():
    return [{"element": e, "position": e} for e in range(1, 16)]


def test_rank_bench_orders_by_xp(db):
    _seed(db)
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (13,30,'v1',3.0,80)")
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (14,30,'v1',5.0,80)")
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (15,30,'v1',1.0,80)")
    db.commit()
    assert bench.rank_bench(db, _picks()) == [14, 13, 15]


def test_rank_bench_missing_xp_sorts_last(db):
    _seed(db)
    db.execute("INSERT INTO xp (player_id, gw, model_version, xp, xminutes) VALUES (13,30,'v1',2.0,80)")
    db.commit()
    out = bench.rank_bench(db, _picks())
    assert out[0] == 13 and set(out) == {13, 14, 15}


def test_rank_bench_only_bench_positions(db):
    _seed(db)
    out = bench.rank_bench(db, _picks())
    assert set(out) == {13, 14, 15}      # positions 1-12 ignored
