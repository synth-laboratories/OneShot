#!/usr/bin/env python3
"""Debug script to see what Groq API actually returns for baseline classification tasks.

Usage:
    python debug_baseline_api.py [--dataset DATASET_NAME] [--task-name TASK_NAME]

Examples:
    python debug_baseline_api.py --dataset PolyAI/banking77 --task-name banking77
    python debug_baseline_api.py --dataset huggingface/glue --task-name sst2
"""

import argparse
import asyncio
import json
import os
import httpx
from datasets import load_dataset
from typing import Optional

async def test_baseline_api(dataset_name: str = "PolyAI/banking77", task_name: Optional[str] = None):
    """Test baseline API with a configurable dataset.
    
    Args:
        dataset_name: HuggingFace dataset identifier (e.g., "PolyAI/banking77")
        task_name: Task name for tool/function naming (defaults to dataset name)
    """
    if task_name is None:
        # Extract task name from dataset name (e.g., "PolyAI/banking77" -> "banking77")
        task_name = dataset_name.split("/")[-1] if "/" in dataset_name else dataset_name
    
    # Load dataset
    print(f"Loading dataset: {dataset_name}")
    dataset = load_dataset(dataset_name)
    label_names = dataset["train"].features["label"].names
    
    # Get first example
    example = dataset["train"][0]
    print(f"Query: {example['text']}")
    print(f"Expected label: {label_names[example['label']]}")
    print()
    
    # Build prompt
    num_labels = len(label_names)
    label_preview = ', '.join(label_names[:10])
    system_prompt = f"""You are an expert assistant that classifies queries.
Given a query, respond with exactly one intent label using the tool call.

Valid intents: {label_preview}... ({num_labels} total)"""
    
    user_prompt = f"Query: {example['text']}\n\nClassify this query."
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    
    # Tool definition
    tool_function_name = f"{task_name}_classify"
    tool = {
        "type": "function",
        "function": {
            "name": tool_function_name,
            "description": f"Classify a {task_name} query into an intent",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "enum": label_names,
                        "description": "The intent label",
                    }
                },
                "required": ["label"],
            },
        },
    }
    
    # Check API key
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set!")
        return
    
    print(f"API Key present: {bool(api_key)}")
    print("Model: llama-3.1-70b-versatile")
    print()
    
    # Make API call
    base_url = "https://api.groq.com/openai/v1"
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        print("Making API call...")
        resp = await http_client.post(
            f"{base_url}/chat/completions",
            json={
                "model": "llama-3.1-70b-versatile",
                "messages": messages,
                "tools": [tool],
                "tool_choice": {"type": "function", "function": {"name": tool_function_name}},
                "temperature": 0.0,
                "max_tokens": 128,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        
        print(f"Status code: {resp.status_code}")
        
        if resp.status_code != 200:
            print("ERROR: API call failed!")
            print(f"Response: {resp.text}")
            return
        
        response = resp.json()
        
        print("\n" + "="*60)
        print("FULL API RESPONSE:")
        print("="*60)
        print(json.dumps(response, indent=2))
        print("="*60)
        print()
        
        # Try to parse like baseline does
        predicted_label = ""
        tool_calls = []
        if "choices" in response and len(response["choices"]) > 0:
            message = response["choices"][0].get("message", {})
            tool_calls = message.get("tool_calls", [])
            print(f"Found tool_calls: {len(tool_calls)}")
            if tool_calls:
                print(f"Tool call: {json.dumps(tool_calls[0], indent=2)}")
        elif "tool_calls" in response:
            tool_calls = response["tool_calls"]
            print(f"Found tool_calls at top level: {len(tool_calls)}")
        
        if tool_calls:
            args = tool_calls[0]["function"].get("arguments", "")
            print(f"Arguments (raw): {args}")
            print(f"Arguments type: {type(args)}")
            if isinstance(args, str):
                args = json.loads(args)
            predicted_label = args.get("label", "") if isinstance(args, dict) else ""
            print(f"Predicted label: {predicted_label}")
        else:
            print("WARNING: No tool_calls found in response!")
            message = response.get("choices", [{}])[0].get("message", {})
            content = message.get("content", "")
            print(f"Message content: {content}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Debug script to test baseline API with configurable datasets"
    )
    parser.add_argument(
        "--dataset",
        default="PolyAI/banking77",
        help="HuggingFace dataset identifier (default: PolyAI/banking77)"
    )
    parser.add_argument(
        "--task-name",
        default=None,
        help="Task name for tool/function naming (defaults to dataset name)"
    )
    args = parser.parse_args()
    
    asyncio.run(test_baseline_api(dataset_name=args.dataset, task_name=args.task_name))


