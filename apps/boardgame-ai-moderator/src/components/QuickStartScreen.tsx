import React, { useState } from 'react';
import { motion } from 'motion/react';
import { 
  ArrowLeft, 
  ArrowRight,
  Trophy,
  RotateCw,
  Crown,
  Users
} from 'lucide-react';
import { getGameInfo } from '../gameDatabase';

const IsometricPawn = ({ active, completed }: { active?: boolean, completed?: boolean, key?: React.Key }) => (
  <motion.div 
    animate={active ? { y: [0, -4, 0] } : {}}
    transition={active ? { repeat: Infinity, duration: 1.5, ease: "easeInOut" } : {}}
    className={`relative w-6 h-8 flex flex-col items-center justify-end ${active ? 'scale-125' : 'scale-100'}`}
  >
    <div className={`w-3 h-3 rounded-full mb-[-4px] relative z-10 ${completed || active ? 'bg-amber-brand' : 'bg-navy-light/30'}`} />
    <div className={`w-4 h-6 rounded-t-full ${completed || active ? 'bg-amber-brand' : 'bg-navy-light/30'}`} />
  </motion.div>
);

const QuickStartCard = ({ 
  label, 
  title, 
  description, 
  icon: Icon,
  children 
}: { 
  label: string, 
  title: string, 
  description: string, 
  icon: React.ElementType,
  children?: React.ReactNode
}) => (
  <div className="bg-white rounded-[16px] shadow-iso-1 overflow-hidden flex flex-col border border-navy-deep/5">
    <div className="h-[180px] bg-amber-light flex items-center justify-center relative overflow-hidden">
      {children ? children : (
        <div className="isometric-container">
          <div className="w-32 h-32 bg-board-beige border-4 border-board-beige-dark isometric-card shadow-iso-2 flex items-center justify-center">
            <Icon size={48} className="text-amber-brand opacity-80" />
          </div>
        </div>
      )}
    </div>
    <div className="p-4 space-y-2">
      <span className="text-[11px] font-sans font-semibold text-navy-light uppercase tracking-[0.08em]">
        {label}
      </span>
      <h3 className="font-display font-bold text-[18px] text-navy-deep">
        {title}
      </h3>
      <p className="font-sans text-[14px] text-navy-mid leading-relaxed">
        {description}
      </p>
    </div>
  </div>
);

interface QuickStartScreenProps {
  onBack: () => void;
  onSkip: () => void;
  onNext: () => void;
  gameName?: string;
}

export const QuickStartScreen = ({ onBack, onSkip, onNext, gameName = "Catan" }: QuickStartScreenProps) => {
  const [step, setStep] = useState(0);
  const game = getGameInfo(gameName);
  const qs = game?.quickStart;

  return (
    <div className="flex flex-col h-screen bg-surface-cream overflow-hidden max-w-md mx-auto relative">
      {/* Top Bar */}
      <header className="h-16 bg-white flex items-center justify-between px-6 z-20 shrink-0">
        <button onClick={onBack} className="p-2 -ml-2 text-navy-deep hover:bg-surface-cream rounded-full transition-colors">
          <ArrowLeft size={24} />
        </button>
        <h1 className="font-display font-bold text-[20px] text-navy-deep">Quick Start</h1>
        <button onClick={onSkip} className="font-sans font-medium text-[14px] text-amber-brand">
          Skip
        </button>
      </header>

      {/* Progress Indicator */}
      <div className="h-12 flex items-center justify-center gap-4 bg-white/50 backdrop-blur-sm shrink-0 border-b border-navy-deep/5">
        {[0, 1, 2, 3, 4].map((i) => (
          <IsometricPawn key={i} active={i === step} completed={i < step} />
        ))}
      </div>

      {/* Main Content Area */}
      <main className="flex-1 overflow-y-auto p-4 space-y-4 pb-32">
        <QuickStartCard 
          label="THE GOAL" 
          title={qs ? "Your objective" : "Be the first to win"}
          description={qs?.goal ?? `In ${gameName}, compete against other players to achieve the victory condition. Learn the goal and start playing!`}
          icon={Trophy}
        />

        <QuickStartCard 
          label="HOW TURNS WORK" 
          title="Turn structure"
          description={qs?.turns ?? `Each turn in ${gameName} follows a sequence of actions. Take your actions, then pass to the next player.`}
          icon={RotateCw}
        >
          <div className="relative w-full h-full flex items-center justify-center">
            <div className="absolute inset-0 bg-amber-light/50" />
            <div className="relative w-40 h-40 flex items-center justify-center">
              <div className="w-32 h-32 rounded-full border-4 border-board-beige-dark bg-board-beige shadow-lg flex items-center justify-center">
                <Users size={40} className="text-navy-mid opacity-20" />
              </div>
              <motion.div 
                className="absolute inset-0 border-[3px] border-dashed border-amber-brand rounded-full"
                animate={{ rotate: 360 }}
                transition={{ duration: 10, repeat: Infinity, ease: "linear" }}
              />
              <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1/2">
                <ArrowRight size={20} className="text-amber-brand rotate-[-90deg]" />
              </div>
            </div>
          </div>
        </QuickStartCard>

        <QuickStartCard 
          label="HOW TO WIN" 
          title="Path to victory"
          description={qs?.winning ?? `Master the strategy of ${gameName} to outplay your opponents and claim victory!`}
          icon={Crown}
        />
      </main>

      {/* Bottom Sticky Area */}
      <div className="absolute bottom-0 left-0 right-0 p-6 bg-gradient-to-t from-surface-cream via-surface-cream to-transparent z-40">
        <motion.button 
          onClick={() => {
            if (step < 4) setStep(step + 1);
            else onNext();
          }}
          whileTap={{ scale: 0.98 }}
          className="w-full h-14 bg-navy-deep text-white rounded-[16px] flex items-center justify-center gap-3 font-display font-bold text-[16px] shadow-iso-2 active:shadow-sm transition-all"
        >
          Next
          <ArrowRight size={20} className="opacity-90" />
        </motion.button>
      </div>
    </div>
  );
};
