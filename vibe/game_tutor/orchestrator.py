from __future__ import annotations

import json
from pathlib import Path

from vibe.game_tutor.models import (
    Board,
    Components,
    DecisionNode,
    DecisionTree,
    FailureResult,
    Hint,
    LegalAction,
    Mechanics,
    ParsedRules,
    Piece,
    PracticePosition,
    RenderedUi,
    Scenario,
    StrategyBundle,
    StrategyModule,
    StrategyOption,
    SuccessResult,
    TutorialBundle,
    TutorialSeriesItem,
)


class RuleParserAgent:
    def parse(self, raw_rules: str, source_name: str = "unknown_game") -> ParsedRules:
        text = raw_rules.lower()
        match self._detect_game(text, source_name):
            case "chess":
                return self._parse_chess()
            case "mahjong":
                return self._parse_mahjong()
            case _:
                return self._parse_generic(source_name, raw_rules)

    def _detect_game(self, text: str, source_name: str) -> str:
        if any(keyword in text for keyword in ["king", "queen", "checkmate", "rook"]):
            return "chess"
        if any(keyword in text for keyword in ["riichi", "pon", "chii", "kan", "tile"]):
            return "mahjong"
        filename = Path(source_name).stem.lower()
        if "chess" in filename:
            return "chess"
        if "mahjong" in filename:
            return "mahjong"
        return "generic"

    def _parse_chess(self) -> ParsedRules:
        return ParsedRules(
            game_name="Chess",
            components=Components(
                pieces=[
                    Piece(name="King", movement="1 square any direction", value=None),
                    Piece(
                        name="Queen",
                        movement="Any squares orthogonal or diagonal",
                        value=9,
                    ),
                    Piece(name="Rook", movement="Any squares orthogonal", value=5),
                    Piece(name="Bishop", movement="Any squares diagonal", value=3),
                    Piece(name="Knight", movement="L-shape", value=3),
                    Piece(name="Pawn", movement="1 square forward", value=1),
                ],
                board=Board(type="grid", size="8x8"),
            ),
            mechanics=Mechanics(
                setup="Standard 32-piece starting position",
                turn_structure=["White moves", "Black moves", "Repeat until mate/draw"],
                legal_actions=[
                    LegalAction(
                        action="move",
                        conditions=["follows piece movement", "does not leave king in check"],
                    ),
                    LegalAction(
                        action="capture",
                        conditions=["land on opponent occupied square", "legal for piece"],
                    ),
                ],
                win_conditions=["Checkmate opponent king"],
                scoring=None,
            ),
            constraints=["No move can leave own king in check"],
        )

    def _parse_mahjong(self) -> ParsedRules:
        return ParsedRules(
            game_name="Riichi Mahjong",
            components=Components(
                board=Board(type="irregular", size="4-player table"),
                resources=["Wall tiles", "Dora indicators", "Points"],
            ),
            mechanics=Mechanics(
                setup="Each player starts with 13 tiles",
                turn_structure=["Draw", "Discard", "Claim window", "Next player"],
                legal_actions=[
                    LegalAction(action="pon", conditions=["discard completes triplet"]),
                    LegalAction(action="chii", conditions=["left discard completes sequence"]),
                    LegalAction(action="kan", conditions=["four identical tiles"]),
                    LegalAction(action="riichi", conditions=["closed hand and tenpai"]),
                ],
                win_conditions=["Complete valid hand and declare ron/tsumo"],
                scoring="Han and fu based scoring",
            ),
            constraints=["Chii only from left player discard"],
        )

    def _parse_generic(self, source_name: str, raw_rules: str) -> ParsedRules:
        setup_line = next((line.strip() for line in raw_rules.splitlines() if line.strip()), "")
        return ParsedRules(
            game_name=Path(source_name).stem.replace("_", " ").title() or "Unknown Game",
            components=Components(),
            mechanics=Mechanics(
                setup=setup_line,
                turn_structure=["Take turns in order"],
                legal_actions=[
                    LegalAction(
                        action="take_turn",
                        conditions=["follow the provided rules text"],
                    )
                ],
                win_conditions=["Achieve stated objective in rules"],
            ),
            constraints=["No action can violate explicit rules text"],
        )


class TutorialGeneratorAgent:
    def generate(self, parsed_rules: ParsedRules) -> TutorialBundle:
        match parsed_rules.game_name.lower():
            case game if "chess" in game:
                return TutorialBundle(tutorial_series=[self._chess_tutorial()])
            case game if "mahjong" in game:
                return TutorialBundle(tutorial_series=[self._mahjong_tutorial()])
            case _:
                return TutorialBundle(tutorial_series=[self._generic_tutorial(parsed_rules.game_name)])

    def _chess_tutorial(self) -> TutorialSeriesItem:
        return TutorialSeriesItem(
            id="chess_fork_basics",
            title="Chess: Fork Fundamentals",
            difficulty=2,
            type="pattern",
            scenarios=[
                Scenario(
                    setup="8/8/8/3N4/8/8/8/8 w - - 0 1",
                    objective="Move the knight to attack two pieces.",
                    valid_moves=["Nc7", "Ne7"],
                    hints=[
                        Hint(trigger="progressive", text="Knights move in an L shape."),
                        Hint(trigger="after_1_fail", text="Target king and rook together."),
                        Hint(trigger="after_2_fails", text="Try squares c7 or e7."),
                    ],
                    success=SuccessResult(
                        move="Nc7",
                        feedback="Great fork. You win material after the king moves.",
                        next_scenario="chess_fork_defense",
                    ),
                    failures=[
                        FailureResult(
                            move_pattern="N.*",
                            feedback="That move does not create a double attack.",
                        )
                    ],
                )
            ],
        )

    def _mahjong_tutorial(self) -> TutorialSeriesItem:
        return TutorialSeriesItem(
            id="mahjong_safe_discard",
            title="Mahjong: Safe Discard Basics",
            difficulty=3,
            type="strategy",
            scenarios=[
                Scenario(
                    setup={
                        "hand": ["1m", "2m", "3m", "5p", "5p", "7s", "8s", "9s", "W", "W", "G", "G", "G"],
                        "discard_pile": ["4m", "6p", "2s", "W", "E"],
                    },
                    objective="Choose a safe discard against visible calls.",
                    valid_moves=["discard:W", "discard:1m", "discard:G"],
                    hints=[
                        Hint(trigger="progressive", text="Start with tiles already seen in discards."),
                        Hint(trigger="after_1_fail", text="An already-discarded honor is often safer."),
                        Hint(trigger="after_2_fails", text="West is safest in this position."),
                    ],
                    success=SuccessResult(
                        move="discard:W",
                        feedback="Correct. West has lower deal-in risk here.",
                        next_scenario="mahjong_suji_reading",
                    ),
                )
            ],
        )

    def _generic_tutorial(self, game_name: str) -> TutorialSeriesItem:
        return TutorialSeriesItem(
            id="core_mechanics",
            title=f"{game_name}: Core Mechanics",
            difficulty=1,
            type="mechanic",
            scenarios=[
                Scenario(
                    setup={"state": "initial"},
                    objective="Take a legal first turn.",
                    valid_moves=["take_turn"],
                    hints=[
                        Hint(trigger="progressive", text="Follow the turn structure exactly."),
                        Hint(trigger="after_1_fail", text="Start by reading setup and constraints."),
                        Hint(trigger="after_2_fails", text="Pick the move marked legal by rules."),
                    ],
                    success=SuccessResult(move="take_turn", feedback="Valid move.", next_scenario=None),
                )
            ],
        )


class StrategyEngineAgent:
    def generate(self, parsed_rules: ParsedRules, tutorials: TutorialBundle) -> StrategyBundle:
        _ = tutorials
        match parsed_rules.game_name.lower():
            case game if "chess" in game:
                return StrategyBundle(strategy_modules=[self._chess_module()])
            case game if "mahjong" in game:
                return StrategyBundle(strategy_modules=[self._mahjong_module()])
            case _:
                return StrategyBundle(strategy_modules=[self._generic_module(parsed_rules.game_name)])

    def _chess_module(self) -> StrategyModule:
        return StrategyModule(
            id="back_rank_mate",
            title="Back Rank Mate",
            category="tactics",
            decision_tree=DecisionTree(
                nodes=[
                    DecisionNode(
                        id="recognition",
                        situation="Enemy king trapped by own pawns on back rank",
                        question="How do you convert this into mate?",
                        options=[
                            StrategyOption(
                                choice="Rook to back rank",
                                evaluation_criteria=["forced check", "escape squares covered"],
                                correct_when="rook_can_reach_back_rank",
                                next_node=None,
                                common_mistake="Delaying allows defender to create luft.",
                            )
                        ],
                    )
                ]
            ),
            practice_positions=[
                PracticePosition(
                    setup="6k1/5ppp/8/8/8/8/5PPP/5RK1 w - - 0 1",
                    optimal_line=["Rf8#"],
                    distractors=["Qf8+"],
                    explanation="Rf8 is immediate checkmate.",
                )
            ],
        )

    def _mahjong_module(self) -> StrategyModule:
        return StrategyModule(
            id="riichi_response",
            title="Responding to Riichi",
            category="defense",
            decision_tree=DecisionTree(
                nodes=[
                    DecisionNode(
                        id="evaluate_hand",
                        situation="Opponent declared riichi",
                        question="Push or fold?",
                        options=[
                            StrategyOption(
                                choice="Push",
                                evaluation_criteria=["shanten <= 1", "safe discard exists"],
                                correct_when="ready_hand_and_reasonable_safety",
                                next_node="pick_push_tile",
                                common_mistake="Pushing with no outs and no safety hemorrhages points.",
                            ),
                            StrategyOption(
                                choice="Fold",
                                evaluation_criteria=["far from tenpai", "dangerous table"],
                                correct_when="shanten > 2_or_no_safe_tiles",
                                next_node=None,
                                common_mistake="Over-folding with a premium hand gives up expected value.",
                            ),
                        ],
                    )
                ]
            ),
            practice_positions=[
                PracticePosition(
                    setup={"state": "riichi_pressure"},
                    optimal_line=["discard:genbutsu"],
                    distractors=["discard:dora"],
                    explanation="Respect danger first when hand value is low.",
                )
            ],
        )

    def _generic_module(self, game_name: str) -> StrategyModule:
        return StrategyModule(
            id="core_strategy",
            title=f"{game_name}: Core Strategy",
            category="opening",
            decision_tree=DecisionTree(
                nodes=[
                    DecisionNode(
                        id="evaluate_state",
                        situation="Beginning of game",
                        question="What should you prioritize first?",
                        options=[
                            StrategyOption(
                                choice="Secure legal objectives",
                                evaluation_criteria=["aligns with win condition"],
                                correct_when="objective_progresses",
                                next_node=None,
                                common_mistake="Random moves without objective focus lose tempo.",
                            )
                        ],
                    )
                ]
            ),
            practice_positions=[
                PracticePosition(
                    setup={"state": "opening"},
                    optimal_line=["objective_aligned_move"],
                    distractors=["cosmetic_move"],
                    explanation="Prioritize moves that increase win probability.",
                )
            ],
        )


class InteractiveBuilderAgent:
    def build(self, tutorials: TutorialBundle, strategies: StrategyBundle) -> list[RenderedUi]:
        tutorial_count = len(tutorials.tutorial_series)
        strategy_count = len(strategies.strategy_modules)
        source = f"""import React from 'react';

export function GameTutorApp() {{
  return (
    <main>
      <h1>Game Tutor</h1>
      <p>Tutorial modules: {tutorial_count}</p>
      <p>Strategy modules: {strategy_count}</p>
      <button>Start Interactive Lesson</button>
    </main>
  );
}}
"""
        return [
            RenderedUi(
                component_name="GameTutorApp",
                file_path="game-tutor/ui/GameTutorApp.tsx",
                source=source,
            )
        ]


class MistralVibeOrchestrator:
    def __init__(self) -> None:
        self.rule_parser = RuleParserAgent()
        self.tutorial_generator = TutorialGeneratorAgent()
        self.strategy_engine = StrategyEngineAgent()
        self.interactive_builder = InteractiveBuilderAgent()

    def run(self, rules_path: Path, output_root: Path) -> dict[str, object]:
        raw_rules = rules_path.read_text(encoding="utf-8")
        parsed = self.rule_parser.parse(raw_rules, source_name=rules_path.name)
        tutorials = self.tutorial_generator.generate(parsed)
        strategies = self.strategy_engine.generate(parsed, tutorials)
        ui_files = self.interactive_builder.build(tutorials, strategies)

        tutorials_dir = output_root / "tutorials"
        strategies_dir = output_root / "strategies"
        ui_dir = output_root / "ui"
        tutorials_dir.mkdir(parents=True, exist_ok=True)
        strategies_dir.mkdir(parents=True, exist_ok=True)
        ui_dir.mkdir(parents=True, exist_ok=True)

        (tutorials_dir / f"{parsed.game_name.lower().replace(' ', '_')}.json").write_text(
            tutorials.model_dump_json(indent=2), encoding="utf-8"
        )
        (strategies_dir / f"{parsed.game_name.lower().replace(' ', '_')}.json").write_text(
            strategies.model_dump_json(indent=2), encoding="utf-8"
        )

        for ui_file in ui_files:
            ui_path = output_root / Path(ui_file.file_path).name
            ui_path.write_text(ui_file.source, encoding="utf-8")

        manifest = {
            "parsed_rules": parsed.model_dump(mode="json"),
            "tutorial_output": str(tutorials_dir),
            "strategy_output": str(strategies_dir),
            "ui_files": [file.file_path for file in ui_files],
        }
        (output_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest
