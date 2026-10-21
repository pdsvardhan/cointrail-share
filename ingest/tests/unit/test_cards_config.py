"""iter-18 M10: cards are edited/added through the API and the cards config file follows."""
import json

import pytest
from fastapi import HTTPException

import config
import routes.cards as rc


def cfg():
    with open(config.CARDS_CONFIG_PATH, encoding="utf-8") as f:
        return {c["last4"]: c for c in json.load(f)["cards"]}


def test_put_updates_the_card_and_the_config_file(conn, make_card):
    card = make_card("Axis", "9320", name="Airtel", statement_day=14, due_day=5)
    out = rc.edit_card(card, rc.CardUpdate(due_day=1, card_name="Airtel Axis"))
    assert (out["card"]["due_day"], out["card"]["card_name"], out["card"]["statement_day"]) == (1, "Airtel Axis", 14)
    assert cfg()["9320"]["due_day"] == 1 and out["config_exported"] == 1


def test_put_replacement_and_deactivation(conn, make_card):
    old = make_card("HDFC", "9434", statement_day=22, due_day=12)
    new = make_card("HDFC", "1111", statement_day=22, due_day=12)
    out = rc.edit_card(old, rc.CardUpdate(active=False, replaced_by_card_id=new, pdf_card_last4="5678"))
    assert (out["card"]["active"], out["card"]["replaced_by_card_id"], out["card"]["pdf_card_last4"]) == (0, new, "5678")
    assert cfg()["9434"]["replaced_by_card_id"] == new and cfg()["9434"]["active"] == 0


@pytest.mark.parametrize("body", [
    {"due_day": 0}, {"due_day": 32}, {"statement_day": 40}, {"pdf_card_last4": "12a4"}, {"pdf_card_last4": "123"},
    {"card_name": "   "},
])
def test_put_rejects_bad_input(conn, make_card, body):
    card = make_card("Axis", "9320", statement_day=14, due_day=5)
    with pytest.raises(HTTPException) as e:
        rc.edit_card(card, rc.CardUpdate(**body))
    assert e.value.status_code == 400


def test_put_rejects_self_replacement_and_unknown_cards(conn, make_card):
    card = make_card("Axis", "9320", statement_day=14, due_day=5)
    with pytest.raises(HTTPException) as e:
        rc.edit_card(card, rc.CardUpdate(replaced_by_card_id=card))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        rc.edit_card(card + 99, rc.CardUpdate(due_day=1))
    assert e.value.status_code == 404


def test_post_creates_a_card(conn, make_card):
    make_card("HDFC", "9434", statement_day=22, due_day=12)
    out = rc.add_card(rc.CardCreate(bank="HDFC", last4="1111", card_name="Swiggy (reissued)", network="MasterCard",
                                    statement_day=22, due_day=12))
    assert (out["card"]["last4"], out["card"]["active"], out["card"]["due_day"]) == ("1111", 1, 12)
    assert set(cfg()) == {"9434", "1111"}
    with pytest.raises(HTTPException) as e:
        rc.add_card(rc.CardCreate(bank="HDFC", last4="1111", statement_day=22, due_day=12))
    assert e.value.status_code == 409


@pytest.mark.parametrize("body", [
    dict(bank="HDFC", last4="11a1", statement_day=22, due_day=12),
    dict(bank="HDFC", last4="11111", statement_day=22, due_day=12),
    dict(bank=" ", last4="1111", statement_day=22, due_day=12),
    dict(bank="HDFC", last4="1111", statement_day=0, due_day=12),
    dict(bank="HDFC", last4="1111", statement_day=22, due_day=31 + 1),
])
def test_post_rejects_bad_input(conn, body):
    with pytest.raises(HTTPException) as e:
        rc.add_card(rc.CardCreate(**body))
    assert e.value.status_code == 400
