from touchstone.client import BuildMetadata, parse_lmstudio_models


def test_parse_lmstudio_models_extracts_quant_and_runtime():
    payload = {"data": [
        {"id": "google/gemma-4-12b-qat", "compatibility_type": "mlx", "quantization": "4bit"},
        {"id": "qwen/q", "compatibility_type": "gguf", "quantization": "Q4_K_M"},
    ]}
    meta = parse_lmstudio_models(payload)
    assert isinstance(meta, BuildMetadata)
    assert meta.runtime == "mlx"  # first/dominant runtime
    assert meta.quant_by_model["google/gemma-4-12b-qat"] == "4bit"


def test_parse_lmstudio_models_empty_on_garbage():
    meta = parse_lmstudio_models({"unexpected": True})
    assert meta.runtime is None
    assert meta.quant_by_model == {}
