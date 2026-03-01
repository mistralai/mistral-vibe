from __future__ import annotations

from pydantic import BaseModel, Field


class Piece(BaseModel):
    name: str
    movement: str
    value: int | None = None


class Board(BaseModel):
    type: str
    size: str


class Components(BaseModel):
    pieces: list[Piece] = Field(default_factory=list)
    board: Board = Field(default_factory=lambda: Board(type="grid", size="unknown"))
    resources: list[str] = Field(default_factory=list)


class LegalAction(BaseModel):
    action: str
    conditions: list[str] = Field(default_factory=list)


class Mechanics(BaseModel):
    setup: str = ""
    turn_structure: list[str] = Field(default_factory=list)
    legal_actions: list[LegalAction] = Field(default_factory=list)
    win_conditions: list[str] = Field(default_factory=list)
    scoring: str | None = None


class ParsedRules(BaseModel):
    game_name: str
    components: Components
    mechanics: Mechanics
    phases: list[str] = Field(default_factory=lambda: ["early", "mid", "end"])
    constraints: list[str] = Field(default_factory=list)


class Hint(BaseModel):
    trigger: str
    text: str


class SuccessResult(BaseModel):
    move: str
    feedback: str
    next_scenario: str | None = None


class FailureResult(BaseModel):
    move_pattern: str
    feedback: str
    allow_retry: bool = True


class Scenario(BaseModel):
    setup: str | dict[str, object]
    objective: str
    valid_moves: list[str]
    hints: list[Hint]
    success: SuccessResult
    failures: list[FailureResult] = Field(default_factory=list)


class TutorialSeriesItem(BaseModel):
    id: str
    title: str
    difficulty: int
    type: str
    scenarios: list[Scenario]


class TutorialBundle(BaseModel):
    tutorial_series: list[TutorialSeriesItem]


class StrategyOption(BaseModel):
    choice: str
    evaluation_criteria: list[str] = Field(default_factory=list)
    correct_when: str
    next_node: str | None = None
    common_mistake: str


class DecisionNode(BaseModel):
    id: str
    situation: str
    question: str
    options: list[StrategyOption]


class DecisionTree(BaseModel):
    nodes: list[DecisionNode]


class PracticePosition(BaseModel):
    setup: str | dict[str, object]
    optimal_line: list[str]
    distractors: list[str]
    explanation: str


class StrategyModule(BaseModel):
    id: str
    title: str
    category: str
    decision_tree: DecisionTree
    practice_positions: list[PracticePosition]


class StrategyBundle(BaseModel):
    strategy_modules: list[StrategyModule]


class RenderedUi(BaseModel):
    component_name: str
    file_path: str
    source: str
