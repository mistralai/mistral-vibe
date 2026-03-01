import React, { useState } from 'react';
import { motion } from 'motion/react';
import { Trophy, Clock, MessageSquare, Scale, Brain, Play, Home, Users } from 'lucide-react';

interface PostGameSummaryProps {
  gameName: string;
  duration: string;
  winnerName: string;
  winnerColor: string;
  stats: {
    disputesResolved: number;
    houseRulesActive: number;
    rulesExplained: number;
  };
  onPlayAgain: () => void;
  onNewGame: () => void;
}

export const PostGameSummary = ({
  gameName = "Catan",
  duration = "1h 24m",
  winnerName = "Alice",
  winnerColor = "bg-player-red",
  stats = {
    disputesResolved: 7,
    houseRulesActive: 3,
    rulesExplained: 12
  },
  onPlayAgain,
  onNewGame
}: PostGameSummaryProps) => {
  const [rating, setRating] = useState<number | null>(null);

  return (
    <div className="flex flex-col h-screen bg-surface-cream overflow-hidden max-w-md mx-auto relative">
      {/* Top Half: Amber Background */}
      <div className="h-1/2 bg-amber-brand relative flex flex-col items-center justify-center overflow-hidden">
        {/* Confetti Cubes */}
        <div className="absolute inset-0 pointer-events-none overflow-hidden">
          {[...Array(12)].map((_, i) => (
            <motion.div
              key={i}
              initial={{ 
                x: Math.random() * 400 - 200, 
                y: Math.random() * 400 - 200, 
                rotateX: 45, 
                rotateZ: -45,
                opacity: 0 
              }}
              animate={{ 
                y: [null, Math.random() * 100 + 200],
                opacity: [0, 0.6, 0],
                rotateZ: [-45, Math.random() * 360]
              }}
              transition={{ 
                duration: Math.random() * 3 + 2, 
                repeat: Infinity,
                delay: Math.random() * 2
              }}
              className={`absolute w-3 h-3 ${['bg-player-red', 'bg-player-blue', 'bg-player-yellow', 'bg-player-green'][i % 4]} shadow-sm`}
              style={{ top: '20%', left: '50%' }}
            />
          ))}
        </div>

        {/* Trophy Illustration */}
        <div className="isometric-container mb-6">
          <motion.div
            initial={{ scale: 0, rotateY: 45, rotateX: 30 }}
            animate={{ scale: 1, rotateY: 0, rotateX: 0 }}
            transition={{ type: 'spring', damping: 15, stiffness: 100 }}
            className="relative"
          >
            {/* Podium */}
            <div className="w-24 h-12 bg-white/20 rounded-lg absolute -bottom-4 left-1/2 -translate-x-1/2 blur-xl" />
            <div className="w-20 h-8 bg-amber-light rounded-md shadow-iso-2 flex items-center justify-center">
              <Trophy size={48} className="text-white drop-shadow-lg" />
            </div>
          </motion.div>
        </div>

        <motion.h1 
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="font-display font-extrabold text-[32px] text-white text-center"
        >
          Game Complete!
        </motion.h1>
        <motion.p 
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
          className="font-sans text-[14px] text-white/80 text-center mt-1"
        >
          {gameName} · {duration}
        </motion.p>
      </div>

      {/* Bottom Half: White Card */}
      <motion.div 
        initial={{ y: '100%' }}
        animate={{ y: 0 }}
        transition={{ type: 'spring', damping: 25, stiffness: 150, delay: 0.2 }}
        className="absolute bottom-0 left-0 right-0 h-[55%] bg-white rounded-t-[32px] shadow-[0_-8px_32px_rgba(0,0,0,0.1)] p-8 flex flex-col"
      >
        {/* Winner Display */}
        <div className="flex flex-col items-center mb-8">
          <div className={`w-16 h-16 ${winnerColor} rounded-2xl shadow-iso-1 flex items-center justify-center mb-3`}>
            <div className="isometric-container">
              <Users size={32} className="text-white" />
            </div>
          </div>
          <h2 className="font-display font-bold text-[22px] text-navy-deep">
            🏆 {winnerName} Wins!
          </h2>
        </div>

        {/* Stats Row */}
        <div className="grid grid-cols-3 gap-4 mb-10">
          <div className="flex flex-col items-center">
            <span className="font-display font-extrabold text-[24px] text-amber-brand">{stats.disputesResolved}</span>
            <span className="font-sans text-[12px] text-navy-mid text-center leading-tight">Disputes Resolved</span>
          </div>
          <div className="flex flex-col items-center">
            <span className="font-display font-extrabold text-[24px] text-amber-brand">{stats.houseRulesActive}</span>
            <span className="font-sans text-[12px] text-navy-mid text-center leading-tight">House Rules Active</span>
          </div>
          <div className="flex flex-col items-center">
            <span className="font-display font-extrabold text-[24px] text-amber-brand">{stats.rulesExplained}</span>
            <span className="font-sans text-[12px] text-navy-mid text-center leading-tight">Rules Explained</span>
          </div>
        </div>

        {/* Rating Prompt */}
        <div className="flex flex-col items-center mb-8">
          <span className="font-sans font-semibold text-[16px] text-navy-deep mb-4">How was your session?</span>
          <div className="flex gap-3">
            {[1, 2, 3, 4, 5].map((val) => (
              <motion.button
                key={val}
                whileTap={{ scale: 0.9 }}
                onClick={() => setRating(val)}
                className={`w-10 h-10 rounded-lg flex items-center justify-center transition-all duration-300 ${
                  rating === val 
                    ? 'bg-amber-brand text-white shadow-iso-2 -translate-y-1' 
                    : 'bg-surface-cream text-navy-mid shadow-sm'
                }`}
              >
                <div className="isometric-container">
                  <span className="font-display font-bold text-[16px]">{val}</span>
                </div>
              </motion.button>
            ))}
          </div>
        </div>

        {/* Bottom CTAs */}
        <div className="mt-auto space-y-3">
          <motion.button
            whileTap={{ scale: 0.98 }}
            onClick={onPlayAgain}
            className="w-full h-14 bg-amber-brand text-white rounded-[16px] font-display font-bold text-[16px] flex items-center justify-center gap-2 shadow-iso-1"
          >
            <Play size={20} />
            Play Again
          </motion.button>
          <motion.button
            whileTap={{ scale: 0.98 }}
            onClick={onNewGame}
            className="w-full h-14 bg-transparent border-2 border-navy-deep text-navy-deep rounded-[16px] font-display font-bold text-[16px] flex items-center justify-center gap-2"
          >
            <Home size={20} />
            New Game
          </motion.button>
        </div>
      </motion.div>
    </div>
  );
};
