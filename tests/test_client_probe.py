from touchstone.client import BuildMetadata, parse_lmstudio_models


def test_parse_lmstudio_models_extracts_quant_and_runtime():
    payload = {
        "data": [
            {"id": "google/gemma-4-12b-qat", "compatibility_type": "mlx", "quantization": "4bit"},
            {"id": "qwen/q", "compatibility_type": "gguf", "quantization": "Q4_K_M"},
        ]
    }
    meta = parse_lmstudio_models(payload)
    assert isinstance(meta, BuildMetadata)
    assert meta.runtime == "mlx"  # first/dominant runtime
    assert meta.quant_by_model["google/gemma-4-12b-qat"] == "4bit"


def test_parse_lmstudio_models_empty_on_garbage():
    meta = parse_lmstudio_models({"unexpected": True})
    assert meta.runtime is None
    assert meta.quant_by_model == {}


def test_parse_lmstudio_models_lists_only_loaded_with_quant():
    payload = {
        "data": [
            {"id": "q@4bit", "state": "loaded", "quantization": "4bit", "loaded_context_length": 8},
            {"id": "q", "state": "not-loaded", "quantization": "4bit"},
        ]
    }
    meta = parse_lmstudio_models(payload)
    assert meta.loaded == [{"id": "q@4bit", "quantization": "4bit", "loaded_context_length": 8}]
    assert set(meta.quant_by_model) == {"q@4bit", "q"}
