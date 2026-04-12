"""
model_utils.py — Use your fine-tuned Flan-T5 SQL model locally
(Use this AFTER running train_model.py)
"""

from transformers import T5Tokenizer, T5ForConditionalGeneration
import torch

_tokenizer = None
_model = None

def load_model(model_path="my_sql_model"):
    global _tokenizer, _model
    if _model is None:
        print(f"Loading model from {model_path}...")
        _tokenizer = T5Tokenizer.from_pretrained(model_path)
        _model = T5ForConditionalGeneration.from_pretrained(model_path)
        _model.eval()
        print("✅ Model loaded")

def generate_sql_local(question: str, model_path="my_sql_model") -> str:
    """Generate SQL from natural language using fine-tuned model."""
    load_model(model_path)

    input_text = "convert to SQL: " + question
    inputs = _tokenizer(input_text, return_tensors="pt", max_length=128, truncation=True)

    with torch.no_grad():
        outputs = _model.generate(
            **inputs,
            max_length=128,
            num_beams=4,
            early_stopping=True
        )

    sql = _tokenizer.decode(outputs[0], skip_special_tokens=True)
    return sql

if __name__ == "__main__":
    # Quick test
    tests = [
        "total sales",
        "top 3 customers by revenue",
        "all orders sorted by amount",
    ]
    for q in tests:
        sql = generate_sql_local(q)
        print(f"Q: {q}")
        print(f"SQL: {sql}")
        print("─" * 40)
