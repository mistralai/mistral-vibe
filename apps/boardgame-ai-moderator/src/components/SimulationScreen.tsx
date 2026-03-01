import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  Play, 
  Pause, 
  ChevronLeft, 
  ChevronRight,
  X
} from 'lucide-react';
import { getGameInfo, type SimStep } from '../gameDatabase';

interface SimulationScreenProps {
  onExit: () => void;
  onStartRealGame: () => void;
  gameName?: string;
}

export const SimulationScreen = ({ onExit, onStartRealGame, gameName = "Catan" }: SimulationScreenProps) => {
  const [currentStep, setCurrentStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  
  const game = getGameInfo(gameName);
  const simulationSteps: SimStep[] = game?.simulationSteps ?? [
    { id: 1, player: "PLAYER 1", playerColor: "text-player-red", narration: `Player 1 takes their first action in ${gameName}.`, piecePosition: { x: 20, y: 20 } },
    { id: 2, player: "PLAYER 2", playerColor: "text-player-blue", narration: `Player 2 responds with a strategic move.`, piecePosition: { x: 60, y: 40 } },
    { id: 3, player: "PLAYER 1", playerColor: "text-player-red", narration: `Player 1 builds on their advantage and advances their position.`, piecePosition: { x: 40, y: 60 } },
  ];
  
  const step = simulationSteps[currentStep];

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (isPlaying) {
      interval = setInterval(() => {
        setCurrentStep((prev) => (prev + 1) % simulationSteps.length);
      }, 4000);
    }
    return () => clearInterval(interval);
  }, [isPlaying]);

  const handleNext = () => {
    setCurrentStep((prev) => (prev + 1) % simulationSteps.length);
    setIsPlaying(false);
  };

  const handlePrev = () => {
    setCurrentStep((prev) => (prev - 1 + simulationSteps.length) % simulationSteps.length);
    setIsPlaying(false);
  };

  return (
    <div className="flex flex-col h-screen bg-[#1A1F3C] overflow-hidden max-w-md mx-auto relative text-white">
      {/* Top Bar */}
      <header className="h-16 flex items-center justify-between px-6 z-30">
        <div className="w-10" /> {/* Spacer */}
        <h1 className="font-display font-bold text-[20px] text-white">Simulation</h1>
        <button 
          onClick={onExit}
          className="text-amber-brand font-sans font-bold text-[14px] px-2 py-1"
        >
          Exit
        </button>
      </header>

      {/* Main Area - Game Board (60%) */}
      <main className="h-[60%] relative flex items-center justify-center overflow-hidden">
        {/* Large Soft Shadow */}
        <div className="absolute w-64 h-64 bg-black/40 blur-[40px] rounded-full transform -rotateX-60 translate-y-20" />

        <div className="isometric-container scale-150 rotate-[15deg]">
          <motion.div 
            className="w-64 h-64 bg-board-beige border-4 border-board-beige-dark isometric-card shadow-iso-3"
            initial={{ rotateX: 35, rotateZ: -45 }}
          >
            {/* Board Grid */}
            <div className="absolute inset-0 grid grid-cols-6 grid-rows-6 opacity-10">
              {Array.from({ length: 36 }).map((_, i) => (
                <div key={i} className="border border-navy-deep" />
              ))}
            </div>

            {/* Animated Piece */}
            <AnimatePresence mode="wait">
              <motion.div 
                key={currentStep}
                className={`absolute w-6 h-6 rounded-full shadow-lg ${step.playerColor.replace('text-', 'bg-')}`}
                initial={{ 
                  left: `${simulationSteps[(currentStep - 1 + simulationSteps.length) % simulationSteps.length].piecePosition.x}%`,
                  top: `${simulationSteps[(currentStep - 1 + simulationSteps.length) % simulationSteps.length].piecePosition.y}%`,
                  z: 0
                }}
                animate={{ 
                  left: `${step.piecePosition.x}%`,
                  top: `${step.piecePosition.y}%`,
                  z: [0, 40, 0],
                  scale: [1, 1.2, 1]
                }}
                transition={{ 
                  duration: 0.8,
                  times: [0, 0.5, 1],
                  ease: "easeInOut"
                }}
                style={{ transformStyle: 'preserve-3d' }}
              >
                {/* Piece Top Detail */}
                <div className="absolute inset-1 bg-white/20 rounded-full" />
              </motion.div>
            </AnimatePresence>

            {/* Other Static Pieces */}
            <div className="absolute top-1/4 right-1/4 w-4 h-4 bg-player-yellow rounded-full opacity-60" />
            <div className="absolute bottom-1/4 left-1/3 w-4 h-4 bg-player-green rounded-full opacity-60" />
          </motion.div>
        </div>
      </main>

      {/* Narration Panel (35%) */}
      <section className="h-[35%] bg-[#252A4A] rounded-t-[28px] p-8 flex flex-col relative z-20">
        {/* Current Player Indicator */}
        <div className="flex items-center gap-2 mb-4">
          <div className="isometric-container">
            <div className={`w-3 h-3 rounded-full ${step.playerColor.replace('text-', 'bg-')} shadow-sm`} />
          </div>
          <span className={`text-[11px] font-sans font-semibold uppercase tracking-wider ${step.playerColor}`}>
            {step.player}'S TURN
          </span>
        </div>

        {/* Narration Text */}
        <div className="flex-1 overflow-y-auto">
          <AnimatePresence mode="wait">
            <motion.p 
              key={currentStep}
              className="font-sans text-[16px] text-white/90 leading-[1.6]"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.3 }}
            >
              {step.narration}
            </motion.p>
          </AnimatePresence>
        </div>

        {/* Bottom Controls */}
        <div className="mt-6 flex flex-col gap-6">
          <div className="flex items-center justify-between px-4">
            <button 
              onClick={handlePrev}
              className="text-white/60 font-sans font-medium text-[14px] flex items-center gap-1"
            >
              <ChevronLeft size={16} /> Previous
            </button>

            <motion.button 
              onClick={() => setIsPlaying(!isPlaying)}
              whileTap={{ scale: 0.9 }}
              className="w-14 h-14 bg-amber-brand rounded-full flex items-center justify-center shadow-lg"
            >
              <div className="isometric-container">
                {isPlaying ? (
                  <Pause size={24} fill="white" className="text-white" />
                ) : (
                  <Play size={24} fill="white" className="text-white ml-1" />
                )}
              </div>
            </motion.button>

            <button 
              onClick={handleNext}
              className="text-white/60 font-sans font-medium text-[14px] flex items-center gap-1"
            >
              Next <ChevronRight size={16} />
            </button>
          </div>

          <button 
            onClick={onStartRealGame}
            className="w-full h-12 border-[1.5px] border-white rounded-[12px] text-white font-sans font-bold text-[14px] hover:bg-white/5 transition-colors"
          >
            Start Real Game
          </button>
        </div>
      </section>
    </div>
  );
};
