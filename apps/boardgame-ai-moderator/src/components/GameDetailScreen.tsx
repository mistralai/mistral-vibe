import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  ArrowLeft, 
  Users, 
  Hourglass, 
  Layers, 
  Zap, 
  TrendingUp, 
  PlayCircle, 
  MessageSquare,
  Flag,
  Plus,
  Settings
} from 'lucide-react';
import { HouseRulesManager } from './HouseRulesManager';

const ModeTile = ({ 
  icon: Icon, 
  name, 
  description, 
  accentColor,
  onClick
}: { 
  icon: React.ElementType, 
  name: string, 
  description: string, 
  accentColor: string,
  onClick?: () => void
}) => (
  <motion.div 
    whileTap={{ scale: 0.98 }}
    onClick={onClick}
    className="bg-white rounded-[16px] shadow-iso-1 overflow-hidden flex flex-col h-[160px] cursor-pointer border border-navy-deep/5"
  >
    <div className={`h-[55%] ${accentColor} flex items-center justify-center`}>
      <div className="isometric-container">
        <div className="w-12 h-12 flex items-center justify-center">
          <Icon size={32} className="text-navy-deep opacity-80" />
        </div>
      </div>
    </div>
    <div className="p-3 flex flex-col justify-center flex-1">
      <h4 className="font-display font-bold text-[14px] text-navy-deep leading-tight">{name}</h4>
      <p className="font-sans text-[12px] text-navy-mid mt-1">{description}</p>
    </div>
  </motion.div>
);

interface GameDetailScreenProps {
  onBack: () => void;
  onStartSession: () => void;
  onSelectMode: (mode: 'quickstart' | 'stepbystep' | 'simulate' | 'ask') => void;
  gameName?: string;
}

export const GameDetailScreen = ({ onBack, onStartSession, onSelectMode, gameName = "Catan" }: GameDetailScreenProps) => {
  const [houseRulesEnabled, setHouseRulesEnabled] = useState(false);
  const [isHouseRulesOpen, setIsHouseRulesOpen] = useState(false);

  return (
    <div className="flex flex-col h-screen bg-surface-cream overflow-hidden max-w-md mx-auto relative">
      {/* Top Bar Overlay */}
      <header className="absolute top-0 left-0 right-0 h-16 flex items-center px-6 z-30">
        <button onClick={onBack} className="p-2 -ml-2 bg-white/80 backdrop-blur-sm text-navy-deep rounded-full shadow-sm transition-colors">
          <ArrowLeft size={24} />
        </button>
      </header>

      {/* Hero Area */}
      <section className="h-[45%] bg-amber-light rounded-b-[32px] relative flex items-center justify-center overflow-hidden shrink-0">
        <div className="isometric-container scale-125">
          <motion.div 
            className="w-64 h-64 bg-board-beige border-4 border-board-beige-dark isometric-card shadow-iso-3"
            initial={{ rotateX: 35, rotateZ: -45, y: 50, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ duration: 0.8 }}
          >
            {/* Board Details */}
            <div className="absolute inset-0 grid grid-cols-6 grid-rows-6 opacity-10">
              {Array.from({ length: 36 }).map((_, i) => (
                <div key={i} className="border border-navy-deep" />
              ))}
            </div>
            {/* Scattered Pieces */}
            <div className="absolute top-1/4 left-1/4 w-4 h-4 bg-player-red rounded-full shadow-sm" style={{ transform: 'translateZ(8px)' }} />
            <div className="absolute bottom-1/3 right-1/4 w-4 h-4 bg-player-blue rounded-full shadow-sm" style={{ transform: 'translateZ(8px)' }} />
            <div className="absolute top-1/2 right-1/3 w-3 h-3 bg-white rotate-45 shadow-sm" style={{ transform: 'translateZ(12px)' }} />
          </motion.div>
        </div>

        {/* Overlaid Bottom Chips */}
        <div className="absolute bottom-6 left-0 right-0 flex justify-center gap-2 px-4">
          <div className="bg-white text-navy-deep px-3 py-2 rounded-[20px] shadow-iso-1 flex items-center gap-1.5 border border-navy-deep/5">
            <Users size={14} />
            <span className="text-[12px] font-sans font-medium">2–6 Players</span>
          </div>
          <div className="bg-white text-navy-deep px-3 py-2 rounded-[20px] shadow-iso-1 flex items-center gap-1.5 border border-navy-deep/5">
            <Hourglass size={14} />
            <span className="text-[12px] font-sans font-medium">90 min avg</span>
          </div>
          <div className="bg-white text-navy-deep px-3 py-2 rounded-[20px] shadow-iso-1 flex items-center gap-1.5 border border-navy-deep/5">
            <Layers size={14} />
            <span className="text-[12px] font-sans font-medium">Medium</span>
          </div>
        </div>
      </section>

      {/* Scrollable Content */}
      <main className="flex-1 overflow-y-auto p-6 pb-32">
        <h1 className="font-display font-extrabold text-[28px] text-navy-deep leading-tight mb-2">
          {gameName}
        </h1>
        <p className="font-sans text-[14px] text-navy-mid leading-relaxed mb-8">
          In Catan, players try to be the dominant force on the island of Catan by building settlements, cities, and roads. On each turn dice are rolled to determine what resources the island produces.
        </p>

        {/* Mode Selection */}
        <section className="mb-8">
          <h3 className="text-[11px] font-sans font-semibold text-navy-light uppercase tracking-[0.08em] mb-4">
            CHOOSE YOUR MODE
          </h3>
          <div className="grid grid-cols-2 gap-4">
            <ModeTile 
              icon={Zap} 
              name="Quick Start" 
              description="5-min overview" 
              accentColor="bg-amber-light" 
              onClick={() => onSelectMode('quickstart')}
            />
            <ModeTile 
              icon={TrendingUp} 
              name="Step-by-Step" 
              description="Full walkthrough" 
              accentColor="bg-[#E3F2FD]" 
              onClick={() => onSelectMode('stepbystep')}
            />
            <ModeTile 
              icon={PlayCircle} 
              name="Simulate" 
              description="Watch a sample round" 
              accentColor="bg-[#E8F5E9]" 
              onClick={() => onSelectMode('simulate')}
            />
            <ModeTile 
              icon={MessageSquare} 
              name="Ask Anything" 
              description="Q&A mode" 
              accentColor="bg-[#F3E5F5]" 
              onClick={() => onSelectMode('ask')}
            />
          </div>
        </section>

        {/* House Rules Toggle */}
        <section className="mb-8">
          <div className="flex items-center justify-between py-2">
            <span className="font-sans font-medium text-[14px] text-navy-deep">
              House Rules
            </span>
            <button 
              onClick={() => setIsHouseRulesOpen(true)}
              className="flex items-center gap-1.5 text-amber-brand text-xs font-bold uppercase tracking-wider"
            >
              <Settings size={14} />
              Manage
            </button>
          </div>
        </section>
      </main>

      {/* House Rules Manager */}
      <HouseRulesManager 
        isOpen={isHouseRulesOpen} 
        onClose={() => setIsHouseRulesOpen(false)} 
      />

      {/* Sticky Bottom CTA */}
      <div className="absolute bottom-0 left-0 right-0 p-6 bg-gradient-to-t from-surface-cream via-surface-cream to-transparent z-40">
        <motion.button 
          onClick={onStartSession}
          whileTap={{ scale: 0.98 }}
          className="w-full h-14 bg-amber-brand text-white rounded-[16px] flex items-center justify-center gap-3 font-display font-bold text-[16px] shadow-[0_8px_16px_rgba(245,166,35,0.4)] active:shadow-sm transition-all"
        >
          <Flag size={20} className="opacity-90" />
          Start Game Session
        </motion.button>
      </div>
    </div>
  );
};
