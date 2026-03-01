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

interface Step {
  id: number;
  title: string;
  description: string;
  illustration: React.ReactNode;
  proTip?: string;
  accentColor: string;
}

const steps: Step[] = [
  {
    id: 1,
    title: "Set up the board",
    description: "Place the hexagonal terrain tiles in a random pattern to create the island of Catan. Surround them with the ocean tiles.",
    accentColor: "bg-amber-light",
    proTip: "Try to keep similar resources apart for a more balanced game!",
    illustration: (
      <div className="isometric-container scale-110">
        <motion.div 
          initial={{ rotateX: 45, rotateZ: -45, y: 20, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          className="w-48 h-48 bg-board-beige border-4 border-board-beige-dark shadow-iso-2 relative"
        >
          <div className="absolute inset-0 grid grid-cols-3 grid-rows-3 gap-1 p-2">
            {Array.from({ length: 9 }).map((_, i) => (
              <div key={i} className="bg-emerald-500/20 border border-emerald-600/30 rounded-sm" />
            ))}
          </div>
        </motion.div>
      </div>
    )
  },
  {
    id: 2,
    title: "Place your settlements",
    description: "Each player places two settlements and two roads. Settlements must be at least two intersections apart.",
    accentColor: "bg-blue-50",
    illustration: (
      <div className="isometric-container scale-110">
        <div className="relative w-48 h-48 bg-board-beige border-4 border-board-beige-dark shadow-iso-2">
          <motion.div 
            initial={{ scale: 0, y: -20 }}
            animate={{ scale: 1, y: 0 }}
            className="absolute top-4 left-4 w-6 h-6 bg-player-red rounded-sm shadow-md"
            style={{ transform: 'translateZ(10px)' }}
          />
          <motion.div 
            initial={{ scale: 0, y: -20 }}
            animate={{ scale: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="absolute bottom-10 right-10 w-6 h-6 bg-player-blue rounded-sm shadow-md"
            style={{ transform: 'translateZ(10px)' }}
          />
        </div>
      </div>
    )
  },
  {
    id: 3,
    title: "Roll for resources",
    description: "On your turn, roll both dice. The sum determines which terrain hexes produce resources for players with adjacent settlements.",
    accentColor: "bg-green-50",
    proTip: "Numbers 6 and 8 are rolled most frequently!",
    illustration: (
      <div className="isometric-container scale-110">
        <div className="flex gap-4">
          <motion.div 
            animate={{ rotate: [0, 90, 180, 270, 360], y: [0, -20, 0] }}
            transition={{ duration: 2, repeat: Infinity }}
            className="w-12 h-12 bg-white rounded-lg shadow-iso-2 flex items-center justify-center border-2 border-navy-deep/5"
          >
            <div className="w-2 h-2 bg-navy-deep rounded-full" />
          </motion.div>
          <motion.div 
            animate={{ rotate: [360, 270, 180, 90, 0], y: [0, -20, 0] }}
            transition={{ duration: 2, repeat: Infinity, delay: 0.1 }}
            className="w-12 h-12 bg-white rounded-lg shadow-iso-2 flex items-center justify-center border-2 border-navy-deep/5"
          >
            <div className="grid grid-cols-2 gap-1">
              <div className="w-2 h-2 bg-navy-deep rounded-full" />
              <div className="w-2 h-2 bg-navy-deep rounded-full" />
            </div>
          </motion.div>
        </div>
      </div>
    )
  }
];

interface StepByStepScreenProps {
  onBack: () => void;
  onComplete: () => void;
  gameName?: string;
}

export const StepByStepScreen = ({ onBack, onComplete, gameName = "Catan" }: StepByStepScreenProps) => {
  const [currentStep, setCurrentStep] = useState(0);
  const step = steps[currentStep];
  const totalSteps = steps.length;

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
      <div className={`h-1/2 w-full ${step.accentColor} transition-colors duration-500 flex items-center justify-center relative`}>
        <AnimatePresence mode="wait">
          <motion.div
            key={currentStep}
            initial={{ opacity: 0, x: 50 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -50 }}
            transition={{ duration: 0.4, ease: "easeOut" }}
            className="w-full h-full flex items-center justify-center"
          >
            {step.illustration}
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
            {steps.map((_, i) => (
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
