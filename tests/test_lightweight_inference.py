import numpy as np

from lightweight_inference import decode_age_gender_outputs
from main import get_collection_source_channel


def test_decodes_age_and_male_gender_outputs():
    result = decode_age_gender_outputs(
        [np.array([[[[0.34]]]], dtype=np.float32), np.array([[0.12, 0.88]], dtype=np.float32)]
    )

    assert result["age"] == 34
    assert result["dominant_gender"] == "Man"
    assert result["gender"]["Man"] > result["gender"]["Woman"]


def test_decodes_outputs_independently_of_their_order():
    result = decode_age_gender_outputs(
        [np.array([[0.91, 0.09]], dtype=np.float32), np.array([[[[0.47]]]], dtype=np.float32)]
    )

    assert result["age"] == 47
    assert result["dominant_gender"] == "Woman"


def test_collection_uses_hikvision_substream_by_default(monkeypatch):
    monkeypatch.delenv("CAMERA_COLLECTION_USE_SUBSTREAM", raising=False)
    assert get_collection_source_channel("501") == "502"
    assert get_collection_source_channel("101") == "102"
    assert get_collection_source_channel("502") == "502"


def test_collection_can_keep_main_stream(monkeypatch):
    monkeypatch.setenv("CAMERA_COLLECTION_USE_SUBSTREAM", "false")
    assert get_collection_source_channel("501") == "501"
