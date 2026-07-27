import pytest

from jbcub_bot.features.directory.matching import fold, skeleton


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
