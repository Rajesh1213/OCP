"""Fine-tuning backends — local (Unsloth/LoRA) and hosted (OpenAI)."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def finetune_local(
    dataset_path: str,
    base_model: str = "unsloth/llama-3.2-3b-instruct",
    output_dir: str = "./ocp-optimizer-lora",
    max_steps: int = 200,
    fmt: str = "alpaca",
) -> str:
    """Run LoRA fine-tuning via Unsloth. Returns path to saved adapter."""
    if importlib.util.find_spec("unsloth") is None:
        raise RuntimeError(
            "Unsloth is not installed. Run: pip install ocp-trainer[unsloth]"
        )

    from unsloth import FastLanguageModel  # type: ignore[import]
    from trl import SFTTrainer  # type: ignore[import]
    from transformers import TrainingArguments  # type: ignore[import]
    from datasets import load_dataset  # type: ignore[import]

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base_model,
        max_seq_length=2048,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=["q_proj", "v_proj"],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing=True,
    )

    dataset = load_dataset("json", data_files=dataset_path, split="train")

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="instruction" if fmt == "alpaca" else "messages",
        max_seq_length=2048,
        args=TrainingArguments(
            output_dir=output_dir,
            max_steps=max_steps,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            warmup_steps=10,
            learning_rate=2e-4,
            fp16=True,
            logging_steps=10,
            save_steps=max_steps,
            report_to="none",
        ),
    )
    trainer.train()
    model.save_pretrained(output_dir)
    tokenizer.save_pretrained(output_dir)
    return output_dir


def finetune_openai(
    dataset_path: str,
    base_model: str = "gpt-4o-mini",
    suffix: str = "ocp-optimizer",
) -> str:
    """Submit an OpenAI fine-tuning job. Returns the fine-tuned model ID."""
    if importlib.util.find_spec("openai") is None:
        raise RuntimeError(
            "openai is not installed. Run: pip install ocp-trainer[openai]"
        )

    import openai  # type: ignore[import]

    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    with open(dataset_path, "rb") as f:
        file_resp = client.files.create(file=f, purpose="fine-tune")

    job = client.fine_tuning.jobs.create(
        training_file=file_resp.id,
        model=base_model,
        suffix=suffix,
    )
    print(f"Fine-tuning job submitted: {job.id}")
    print(f"Monitor at: https://platform.openai.com/finetune/{job.id}")
    return job.id


def register_ollama(adapter_path: str, model_name: str = "ocp-optimizer") -> None:
    """Create an Ollama model from a LoRA adapter via a Modelfile."""
    import subprocess

    modelfile_path = Path(adapter_path) / "Modelfile"
    modelfile_path.write_text(
        f"FROM llama3.2\nADAPTER {adapter_path}\n"
        "PARAMETER temperature 0.1\n"
        'SYSTEM "You are a prompt optimizer. Compress and improve the given prompt."\n'
    )

    result = subprocess.run(
        ["ollama", "create", model_name, "-f", str(modelfile_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ollama create failed:\n{result.stderr}")
    print(f"Model registered: {model_name}")
    print(f"Use it by setting: OCP_LOCAL_MODEL={model_name}")
