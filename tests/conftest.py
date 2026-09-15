import pytest

from fixtures import SCHEDULE, make_doc


@pytest.fixture
def doc():
    return make_doc([SCHEDULE], spans=[{0: 3, 5: 3}])


@pytest.fixture
def view(doc):
    from draaiboek.reader import parse_document
    return parse_document(doc)
