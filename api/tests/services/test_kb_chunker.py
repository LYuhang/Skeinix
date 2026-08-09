from vibecanvas_api.services.parsers.base import ParsedSegment
from vibecanvas_api.services.kb_chunker import RecursiveTokenChunker


def test_short_text_not_split():
    segs = [ParsedSegment(text="hello world", metadata={"page": 1})]
    chunks = RecursiveTokenChunker(chunk_size=500, overlap=100).split(segs)
    assert len(chunks) == 1
    assert chunks[0].text == "hello world"
    assert chunks[0].metadata == {"page": 1, "chunk_index": 0}


def test_metadata_propagated():
    segs = [
        ParsedSegment(text="a" * 100, metadata={"page": 1}),
        ParsedSegment(text="b" * 100, metadata={"page": 2}),
    ]
    chunks = RecursiveTokenChunker(chunk_size=500, overlap=50).split(segs)
    assert {c.metadata["page"] for c in chunks} == {1, 2}


def test_long_text_recursive_split_with_overlap():
    # Repeated sentence — must be split, adjacent chunks must share overlap tokens
    sentence = "This is a test sentence. " * 200  # roughly 1000+ tokens
    segs = [ParsedSegment(text=sentence, metadata={})]
    chunks = RecursiveTokenChunker(chunk_size=100, overlap=20).split(segs)
    assert len(chunks) > 1
    # chunk_index monotonically increasing
    assert [c.metadata["chunk_index"] for c in chunks] == list(range(len(chunks)))


def test_no_cross_segment_chunks():
    """Chunks must never span two ParsedSegment boundaries."""
    segs = [
        ParsedSegment(text="A" * 50, metadata={"page": 1}),
        ParsedSegment(text="B" * 50, metadata={"page": 2}),
    ]
    chunks = RecursiveTokenChunker(chunk_size=500, overlap=100).split(segs)
    for c in chunks:
        assert ("A" in c.text) != ("B" in c.text)  # exactly one of the two
