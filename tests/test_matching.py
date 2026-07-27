import pytest

from jbcub_bot.features.directory.matching import (
    ACCEPT,
    fold,
    score,
    skeleton,
    word_score,
)


@pytest.mark.parametrize("text,expected", [
    ("José", "jose"), ("Jose'", "jose"), ("JOSE", "jose"),
    ("Hüseyn", "huseyn"), ("Пётр", "петр"), ("Петр", "петр"),
    ("Андрей", "андреи"), ("  Ivan  ", "ivan"), ("Ben-Othman", "benothman"),
])
def test_fold_strips_decoration(text, expected):
    assert fold(text) == expected


@pytest.mark.parametrize("spellings,expected", [
    (["Ярослав", "Iaroslav", "Yaroslav", "Jaroslav"], "AROSLAV"),
    (["Пётр", "Петр", "Petr", "Pyotr", "Piotr"], "PETR"),
    (["Алексей", "Alexey", "Aleksei"], "ALEKSEI"),
    (["Щеглов", "Scheglov", "Shcheglov"], "SEGLOV"),
    (["Хусейн", "Huseyn", "Khuseyn"], "HUSEIN"),
    (["Ефременко", "Efremenko"], "EFREMENKO"),
    (["Цветков", "Tsvetkov"], "ZVETKOV"),
])
def test_one_skeleton_per_name(spellings, expected):
    assert {skeleton(s) for s in spellings} == {expected}


def test_skeleton_keeps_a_latin_name_intact():
    # Word-initial jo survives; collapsing it would leave "ose".
    assert skeleton("Jose") == "JOSE"


@pytest.mark.parametrize("query,tokens", [
    ("Ярослав", ["Iaroslav", "Belozerov"]),
    ("Yaroslav", ["Iaroslav", "Belozerov"]),
    ("Белозеров", ["Iaroslav", "Belozerov"]),
    ("Ярослав Белозеров", ["Iaroslav", "Belozerov"]),
    ("Belozerov Iaroslav", ["Iaroslav", "Belozerov"]),
    ("Хусейн", ["Huseyn", "Huseynov"]),
    ("Щеглов", ["Igor", "Chsheglov"]),
    ("Кокеридзе", ["Nika", "Kokheridze"]),
    ("Апхазава", ["David", "Apkhazava"]),
    ("Бен Отман", ["Mohamed", "Aziz", "Ben", "Othman"]),
    ("ben othman", ["Mohamed", "Aziz", "Ben", "Othman"]),
    ("Ярослава", ["Iaroslav", "Belozerov"]),
])
def test_a_name_is_found(query, tokens):
    assert score(query, tokens) >= ACCEPT


@pytest.mark.parametrize("query,tokens", [
    ("Иванов", ["Ivan", "Osipenko"]),
    ("привет", ["Pavel", "Egorov"]),
    ("спасибо", ["Pavel", "Egorov"]),
    ("как дела", ["Jessica", "Nasser"]),
    ("кто такой Ярослав", ["Iaroslav", "Belozerov"]),
    ("Petr", ["Mert", "Beren"]),
    ("Zzzzzz", ["Mohamed", "Aziz"]),
])
def test_not_a_name_stays_below_the_threshold(query, tokens):
    assert score(query, tokens) < ACCEPT


def test_query_longer_than_the_name_scores_zero():
    assert score("Ivan Ivanov Ivanovich", ["Ivan", "Ivanov"]) == 0.0


def test_empty_query_scores_zero():
    assert score("   ", ["Ivan"]) == 0.0


def test_word_score_ignores_an_empty_token():
    assert word_score("Ivan", "") == 0.0
