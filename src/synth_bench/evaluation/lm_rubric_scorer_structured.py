#!/usr/bin/env python3
"""
Structured LM-based rubric scorer using OpenAI's structured outputs.
"""

import json
import os
import time
import asyncio
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from openai import AsyncOpenAI

try:
    import tqdm.asyncio as tqdm_async
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False
    tqdm_async = None


@dataclass
class RubricScore:
    """Individual rubric score with reasoning."""
    rubric_id: str
    score: float  # 0.0 to 1.0
    reasoning: str
    evidence: str
    suggestions: Optional[str] = None


@dataclass
class TaskEvaluationResult:
    """Complete task evaluation result."""
    weighted_score: float  # 0.0 to 1.0
    rubric_scores: List[RubricScore]
    summary: str
    metadata: Dict[str, Any]


class LMRubricScorerStructured:
    """LM-based rubric scorer using structured outputs for objective evaluation."""
    
    def __init__(self, model: str = "gpt-5-nano", temperature: float = 1.0):
        self.model = model
        # gpt-5-nano only supports temperature=1
        self.temperature = 1.0 if model == "gpt-5-nano" else temperature
        self.client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
    
    async def evaluate_single_rubric(
        self,
        rubric: Dict[str, Any],
        task_id: str,
        instructions: str,
        diff_content: str,
        test_results: Dict[str, Any],
        files: Dict[str, str],
    ) -> RubricScore:
        """Evaluate a single rubric in parallel."""
        # Build prompt for single rubric
        prompt = self._build_single_rubric_prompt(
            task_id=task_id,
            instructions=instructions,
            rubric=rubric,
            diff_content=diff_content,
            test_results=test_results,
            files=files
        )
        
        # Define the structured response schema for single rubric
        response_schema = {
            "type": "object",
            "properties": {
                "rubric_id": {"type": "string"},
                "score": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
                "evidence": {"type": "string"},
                "suggestions": {"type": "string"}
            },
            "required": ["rubric_id", "score", "reasoning", "evidence"]
        }
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert code reviewer evaluating how well an AI agent completed a programming task. Provide objective, evidence-based scoring."
                    },
                    {
                        "role": "user", 
                        "content": prompt
                    }
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "rubric_evaluation",
                        "schema": response_schema
                    }
                }
            )
            
            # Parse response - handle both string and dict responses
            content = response.choices[0].message.content
            if isinstance(content, str):
                result_json = json.loads(content)
            else:
                result_json = content
            
            # Ensure rubric_id matches
            if result_json.get("rubric_id") != rubric["id"]:
                result_json["rubric_id"] = rubric["id"]
            
            return RubricScore(
                rubric_id=result_json["rubric_id"],
                score=float(result_json["score"]),
                reasoning=str(result_json["reasoning"]),
                evidence=str(result_json["evidence"]),
                suggestions=result_json.get("suggestions")
            )
        except Exception as e:
            # Return fallback score
            return RubricScore(
                rubric_id=rubric["id"],
                score=0.0,
                reasoning=f"Evaluation failed: {str(e)}",
                evidence="",
                suggestions="Manual review recommended"
            )
    
    async def evaluate_task(
        self, 
        task_meta: Dict[str, Any], 
        artifacts: Dict[str, Any]
    ) -> TaskEvaluationResult:
        """Evaluate a task against its rubrics using structured LM scoring."""
        
        # Extract key information
        task_id = task_meta.get("task_id", "unknown")
        instructions = task_meta.get("lm", {}).get("instructions", "")
        rubrics = task_meta.get("evaluation", {}).get("rubrics", [])
        
        # Get diff content
        diff_content = artifacts.get("diff", "")
        if not diff_content.strip():
            diff_content = "No changes made"
        
        # Get test results
        test_results = artifacts.get("test_results", {})
        
        # Get file contents
        files = artifacts.get("files", {})
        
        print("\n🤖 LM EVALUATION STARTING")
        print(f"Model: {self.model}")
        print(f"Temperature: {self.temperature}")
        print(f"Evaluating {len(rubrics)} rubrics in parallel...")
        
        start_time = time.time()
        
        try:
            # Evaluate all rubrics in parallel with progress bar
            if TQDM_AVAILABLE and tqdm_async:
                # Use tqdm.asyncio.tqdm.gather for progress tracking
                tasks = [
                    self.evaluate_single_rubric(
                        rubric=rubric,
                        task_id=task_id,
                        instructions=instructions,
                        diff_content=diff_content,
                        test_results=test_results,
                        files=files,
                    )
                    for rubric in rubrics
                ]
                rubric_scores = await tqdm_async.tqdm.gather(*tasks, desc="Evaluating rubrics", unit="rubric")
            else:
                # Fallback without progress bar
                tasks = [
                    self.evaluate_single_rubric(
                        rubric=rubric,
                        task_id=task_id,
                        instructions=instructions,
                        diff_content=diff_content,
                        test_results=test_results,
                        files=files,
                    )
                    for rubric in rubrics
                ]
                rubric_scores = await asyncio.gather(*tasks)
            
            elapsed_time = time.time() - start_time
            print(f"⚡ All {len(rubrics)} rubrics evaluated in {elapsed_time:.2f}s")
            
            # Calculate weighted scores
            total_weighted_score = 0.0
            total_weight = 0.0
            
            for score_obj in rubric_scores:
                # Find the weight for this rubric
                weight = 0.0
                for rubric in rubrics:
                    if rubric["id"] == score_obj.rubric_id:
                        weight = rubric["weight"]
                        break
                
                # Calculate weighted contribution
                total_weighted_score += score_obj.score * weight
                total_weight += weight
            
            # Calculate final weighted score
            weighted_score = total_weighted_score / total_weight if total_weight > 0 else 0.0
            
            # Generate summary
            summary = self._generate_summary(rubric_scores, rubrics)
            
            print("\n📊 LM EVALUATION RESULTS:")
            print(f"{'='*60}")
            print(f"| {'Rubric':<15} | {'Score':<8} | {'LM Judge':<20} |")
            print(f"{'='*60}")
            
            for score_obj in rubric_scores:
                score_pct = f"{score_obj.score:.0%}"
                reasoning_short = score_obj.reasoning[:17] + "..." if len(score_obj.reasoning) > 20 else score_obj.reasoning
                print(f"| {score_obj.rubric_id:<15} | {score_pct:<8} | {reasoning_short:<20} |")
            
            print(f"{'='*60}")
            print(f"| {'FINAL LM SCORE':<15} | {weighted_score:.0%}    | {'Weighted Average':<20} |")
            print(f"{'='*60}")
            
            return TaskEvaluationResult(
                weighted_score=weighted_score,
                rubric_scores=list(rubric_scores),
                summary=summary,
                metadata={
                    "model": self.model,
                    "temperature": self.temperature,
                    "total_weight": total_weight,
                    "evaluation_time_seconds": elapsed_time
                }
            )
            
        except Exception as e:
            # Fallback evaluation if API fails
            print(f"Warning: LM evaluation failed: {e}")
            return self._fallback_evaluation(rubrics, test_results)
    
    def _build_evaluation_prompt(
        self,
        task_id: str,
        instructions: str,
        rubrics: List[Dict[str, Any]],
        diff_content: str,
        test_results: Dict[str, Any],
        files: Dict[str, str]
    ) -> str:
        """Build the evaluation prompt for the LM."""
        
        prompt = f"""# Task Evaluation

## Task ID: {task_id}

## Original Instructions:
{instructions}

## Agent's Changes (diff):
```diff
{diff_content}
```

## Test Results:
"""
        
        for test_path, result in test_results.items():
            status = "PASSED" if result.get("success", False) else "FAILED"
            prompt += f"- {test_path}: {status}\n"
        
        if files:
            prompt += "\n## Relevant Files After Changes:\n"
            for filename, content in files.items():
                # Truncate very long files
                if len(content) > 2000:
                    content = content[:2000] + "\n... (truncated)"
                prompt += f"\n### {filename}:\n```\n{content}\n```\n"
        
        prompt += "\n## Rubrics to Evaluate:\n"
        for rubric in rubrics:
            prompt += f"""
### {rubric['id']} (weight: {rubric['weight']:.0%})
**Criterion:** {rubric['criterion']}
"""
        
        prompt += """
## Instructions:
Evaluate how well the agent completed the task against each rubric criterion. For each rubric:

1. **Score** (0.0-1.0): How well the criterion was met
   - 1.0 = Fully met the criterion
   - 0.7-0.9 = Mostly met with minor issues  
   - 0.4-0.6 = Partially met
   - 0.1-0.3 = Barely met
   - 0.0 = Not met at all

2. **Reasoning**: Clear explanation of the score based on evidence

3. **Evidence**: Specific examples from the diff/files supporting your assessment

4. **Suggestions**: Optional recommendations for improvement

Be objective and base your evaluation on concrete evidence from the changes made and test results.
"""
        
        return prompt
    
    def _build_single_rubric_prompt(
        self,
        task_id: str,
        instructions: str,
        rubric: Dict[str, Any],
        diff_content: str,
        test_results: Dict[str, Any],
        files: Dict[str, str]
    ) -> str:
        """Build the evaluation prompt for a single rubric."""
        
        prompt = f"""# Task Evaluation - Single Rubric

## Task ID: {task_id}

## Original Instructions:
{instructions}

## Agent's Changes (diff):
```diff
{diff_content}
```

## Test Results:
"""
        
        for test_path, result in test_results.items():
            status = "PASSED" if result.get("success", False) else "FAILED"
            prompt += f"- {test_path}: {status}\n"
        
        if files:
            prompt += "\n## Relevant Files After Changes:\n"
            for filename, content in files.items():
                # Truncate very long files
                if len(content) > 2000:
                    content = content[:2000] + "\n... (truncated)"
                prompt += f"\n### {filename}:\n```\n{content}\n```\n"
        
        prompt += f"""
## Rubric to Evaluate:

### {rubric['id']} (weight: {rubric['weight']:.0%})
**Criterion:** {rubric['criterion']}
"""
        
        # Add evaluation criteria if available
        if "evaluation_criteria" in rubric:
            prompt += "\n**Evaluation Criteria:**\n"
            for criterion in rubric["evaluation_criteria"]:
                prompt += f"- {criterion}\n"
        
        prompt += """
## Instructions:
Evaluate how well the agent completed the task against this rubric criterion:

1. **Score** (0.0-1.0): How well the criterion was met
   - 1.0 = Fully met the criterion
   - 0.7-0.9 = Mostly met with minor issues  
   - 0.4-0.6 = Partially met
   - 0.1-0.3 = Barely met
   - 0.0 = Not met at all

2. **Reasoning**: Clear explanation of the score based on evidence

3. **Evidence**: Specific examples from the diff/files supporting your assessment

4. **Suggestions**: Optional recommendations for improvement

Be objective and base your evaluation on concrete evidence from the changes made and test results.

Return your evaluation as JSON with fields: rubric_id, score, reasoning, evidence, suggestions.
"""
        
        return prompt
    
    def _generate_summary(
        self,
        rubric_scores: List[RubricScore],
        rubrics: List[Dict[str, Any]]
    ) -> str:
        """Generate a summary of the evaluation results."""
        summary_parts = []
        
        # Count pass/fail
        passed = sum(1 for rs in rubric_scores if rs.score >= 1.0)
        partial = sum(1 for rs in rubric_scores if 0.5 <= rs.score < 1.0)
        failed = sum(1 for rs in rubric_scores if rs.score < 0.5)
        
        summary_parts.append(f"Evaluated {len(rubric_scores)} rubrics: {passed} passed, {partial} partial, {failed} failed.")
        
        # Add brief notes on each rubric
        for rs in rubric_scores:
            rubric_name = next((r.get("criterion", rs.rubric_id) for r in rubrics if r["id"] == rs.rubric_id), rs.rubric_id)
            summary_parts.append(f"- {rubric_name}: {rs.score:.0%} - {rs.reasoning[:50]}...")
        
        return " ".join(summary_parts)
    
    def _fallback_evaluation(
        self, 
        rubrics: List[Dict[str, Any]], 
        test_results: Dict[str, Any]
    ) -> TaskEvaluationResult:
        """Fallback evaluation when LM scoring fails."""
        
        rubric_scores = []
        total_weighted_score = 0.0
        total_weight = 0.0
        
        for rubric in rubrics:
            # Simple heuristic: if any related test passed, give partial credit
            score = 0.5  # Default neutral score
            
            for test_path, result in test_results.items():
                if result.get("success", False):
                    score = 0.8  # Give credit for passing tests
                    break
            
            rubric_score = RubricScore(
                rubric_id=rubric["id"],
                score=score,
                reasoning="Fallback evaluation due to LM scorer failure",
                evidence="Based on test results only",
                suggestions="Manual review recommended"
            )
            rubric_scores.append(rubric_score)
            
            weight = rubric["weight"]
            total_weighted_score += score * weight
            total_weight += weight
        
        weighted_score = total_weighted_score / total_weight if total_weight > 0 else 0.0
        
        return TaskEvaluationResult(
            weighted_score=weighted_score,
            rubric_scores=rubric_scores,
            summary="Fallback evaluation completed due to LM scorer failure",
            metadata={"fallback": True}
        )