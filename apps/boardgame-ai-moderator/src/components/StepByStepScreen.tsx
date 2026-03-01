import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  ArrowLeft, 
  Lightbulb, 
  ChevronRight,
  Box,
  Layout,
  Layers,
  Users,
  Hand,
  Dices,
  Trophy
} from 'lucide-react';
import { getGameInfo, type StepData } from '../gameDatabase';

const ACCENT_COLORS = ["bg-amber-light", "bg-blue-50", "bg-green-50", "bg-purple-50", "bg-rose-50", "bg-cyan-50"];

const STEP_ICONS = [Box, Layout, Dices, Hand, Trophy, Layers];

function makeIllustration(stepIndex: number, accentColor: string) {
  const Icon = STEP_ICONS[stepIndex % STEP_ICONS.length];
  return (
    <div className="isometric-container scale-110">
      <motion.div 
        initial={{ rotateX: 45, rotateZ: -45, y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        className="w-48 h-48 bg-board-beige border-4 border-board-beige-dark shadow-iso-2 relative flex items-center justify-center"
      >
        <Icon size={64} className="text-amber-brand opacity-60" />
      </motion.div>
    </div>
  );
}

interface StepByStepScreenProps {
  onBack: () => void;
  onComplete: () => void;
  gameName?: string;
}

export const StepByStepScreen = ({ onBack, onComplete, gameName = "Catan" }: StepByStepScreenProps) => {
  const [currentStep, setCurrentStep] = useState(0);
  const game = getGameInfo(gameName);
  
  const dynamicSteps = game?.steps ?? [
    { id: 1, title: `Set up ${gameName}`, description: `Prepare the ${gameName} board and components according to the rulebook.` },
    { id: 2, title: "Learn the basics", description: `Understand the core mechanics and turn structure of ${gameName}.` },
    { id: 3, title: "Play your first turn", description: "Follow the turn sequence and make your first moves." },
    { id: 4, title: "Win the game", description: `Work towards the victory condition and enjoy ${gameName}!` },
  ];
  
  const step = dynamicSteps[currentStep];
  const totalSteps = dynamicSteps.length;
  const accentColor = ACCENT_COLORS[currentStep % ACCENT_COLORS.length];

  const handleNext = () => {
    if (currentStep < totalSteps - 1) {
      setCurrentStep(currentStep + 1);
    } else {
      onComplete();
    }
  };

  const handlePrev = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1);
    } else {
      onBack();
    }
  };

  return (
    <div className="flex flex-col h-screen bg-surface-cream overflow-hidden max-w-md mx-auto relative">
      {/* Top Illustration Zone (50%) */}
      <div className={`h-1/2 w-full ${accentColor} transition-colors duration-500 flex items-center justify-center relative`}>
        <AnimatePresence mode="wait">
          <motion.div
            key={currentStep}
            initial={{ opacity: 0, x: 50 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -50 }}
            transition={{ duration: 0.4, ease: "easeOut" }}
            className="w-full h-full flex items-center justify-center"
          >
            {makeIllustration(currentStep, accentColor)}
          </motion.div>
        </AnimatePresence>
        
        {/* Back Button Overlay */}
        <button 
          onClick={onBack}
          className="absolute top-6 left-6 p-2 bg-white/80 backdrop-blur-sm text-navy-deep rounded-full shadow-sm z-20"
        >
          <ArrowLeft size={24} />
        </button>
      </div>

      {/* Instruction Panel (50%) */}
      <motion.div 
        className="h-1/2 bg-white rounded-t-[24px] shadow-[0_-8px_24px_rgba(0,0,0,0.08)] relative -mt-6 z-10 flex flex-col"
        initial={{ y: 100 }}
        animate={{ y: 0 }}
      >
        <div className="flex-1 p-8 pt-10 overflow-y-auto">
          <span className="text-[11px] font-sans font-bold text-amber-brand uppercase tracking-[0.12em] block mb-2">
            STEP {currentStep + 1} OF {totalSteps}
          </span>
          
          <AnimatePresence mode="wait">
            <motion.div
              key={currentStep}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.3 }}
            >
              <h2 className="font-display font-bold text-[22px] text-navy-deep mb-4 leading-tight">
                {step.title}
              </h2>
              <p className="font-sans text-[15px] text-navy-mid leading-[1.6] mb-6">
                {step.description}
              </p>

              {step.proTip && (
                <div className="bg-amber-light/40 rounded-xl p-3 flex items-start gap-3 border border-amber-brand/10">
                  <div className="isometric-container shrink-0 mt-0.5">
                    <Lightbulb size={18} className="text-amber-brand" />
                  </div>
                  <p className="font-sans text-[13px] text-navy-deep italic font-medium leading-snug">
                    <span className="font-bold not-italic mr-1">Pro tip:</span>
                    {step.proTip}
                  </p>
                </div>
              )}
            </motion.div>
          </AnimatePresence>
        </div>

        {/* Bottom Row Navigation */}
        <div className="p-6 pt-2 flex items-center justify-between border-t border-navy-deep/5">
          <button 
            onClick={handlePrev}
            className="font-sans font-medium text-[14px] text-navy-light px-4 py-2"
          >
            Back
          </button>

          {/* Step Dots */}
          <div className="flex gap-2">
            {dynamicSteps.map((_, i) => (
              <div 
                key={i}
                className={`w-2 h-2 rounded-full transition-all duration-300 ${
                  i === currentStep ? 'bg-amber-brand w-4' : 'bg-navy-light/30'
                }`}
              />
            ))}
          </div>

          <motion.button 
            onClick={handleNext}
            whileTap={{ scale: 0.95 }}
            className="h-12 w-[120px] bg-amber-brand text-white rounded-[12px] font-display font-bold text-[15px] shadow-iso-1 flex items-center justify-center gap-2"
          >
            {currentStep === totalSteps - 1 ? 'Finish' : 'Next'}
            <ChevronRight size={18} />
          </motion.button>
        </div>
      </motion.div>
    </div>
  );
};
