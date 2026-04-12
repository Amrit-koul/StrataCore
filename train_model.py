"""
train_model.py — Fine-tune Flan-T5 on custom Text-to-SQL dataset
Run: python train_model.py
"""

import os
import json
from transformers import T5Tokenizer, T5ForConditionalGeneration, TrainingArguments, Trainer
from datasets import Dataset

# ── 1. Load Dataset ────────────────────────────────────────────────
with open("data.json") as f:
    data = json.load(f)

dataset = Dataset.from_list(data)
print(f"✅ Loaded {len(dataset)} training examples")

# ── 2. Load Model ─────────────────────────────────────────────────
model_name = "google/flan-t5-small"
tokenizer = T5Tokenizer.from_pretrained(model_name)
model = T5ForConditionalGeneration.from_pretrained(model_name)
print(f"✅ Loaded base model: {model_name}")

# ── 3. Tokenize ───────────────────────────────────────────────────
def preprocess(example):
    input_text = "convert to SQL: " + example["input"]
    target_text = example["output"]
    inputs = tokenizer(input_text, padding="max_length", truncation=True, max_length=128, return_tensors="pt")
    targets = tokenizer(target_text, padding="max_length", truncation=True, max_length=128, return_tensors="pt")
    result = {k: v.squeeze(0) for k, v in inputs.items()}
    result["labels"] = targets["input_ids"].squeeze(0)
    return result

tokenized = dataset.map(preprocess, remove_columns=["input", "output"])
print("✅ Dataset tokenized")

# ── 4. Training Config ────────────────────────────────────────────
training_args = TrainingArguments(
    output_dir="./checkpoints",
    per_device_train_batch_size=4,
    num_train_epochs=10,
    logging_steps=10,
    save_steps=100,
    learning_rate=3e-4,
    warmup_steps=10,
    weight_decay=0.01,
    report_to="none",
    no_cuda=True,
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=tokenized,
)

# ── 5. Train ──────────────────────────────────────────────────────
print("🚀 Starting training... (this may take a few minutes)")
trainer.train()

# ── 6. Save ───────────────────────────────────────────────────────
model.save_pretrained("my_sql_model")
tokenizer.save_pretrained("my_sql_model")
print("✅ Model saved to ./my_sql_model")
print("🎉 Training complete! Now run: python app.py")
