import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  Clock, 
  Layers, 
  MessageSquare, 
  Swords, 
  Pencil, 
  HelpCircle,
  MoreVertical
} from 'lucide-react';
import { RuleQAOverlay } from './RuleQAOverlay';
import { DisputeResolutionScreen } from './DisputeResolutionScreen';
import { HouseRulesManager } from './HouseRulesManager';

interface Player {
  id: number;
  name: string;
  color: string;
  isActive: boolean;
}

const INITIAL_PLAYERS: Player[] = [
  { id: 1, name: "Alex", color: "#FF5252", isActive: true },
  { id: 2, name: "Jordan", color: "#448AFF", isActive: false },
  { id: 3, name: "Sam", color: "#4CAF50", isActive: false },
  { id: 4, name: "Casey", color: "#FFD740", isActive: false },
];

interface ActiveSessionScreenProps {
  gameName?: string;
  onExit?: () => void;
  onFinish?: () => void;
}

export const ActiveSessionScreen = ({ gameName = "Catan", onExit, onFinish }: ActiveSessionScreenProps) => {
  const [players, setPlayers] = useState<Player[]>(INITIAL_PLAYERS);
  const [timer, setTimer] = useState("01:24:35");
  const [isFabOpen, setIsFabOpen] = useState(false);
  const [isQAOpen, setIsQAOpen] = useState(false);
  const [isDisputeOpen, setIsDisputeOpen] = useState(false);
  const [isHouseRulesOpen, setIsHouseRulesOpen] = useState(false);

  const activePlayer = players.find(p => p.isActive) || players[0];

  const toggleFab = () => setIsFabOpen(!isFabOpen);

  const fabOptions = [
    { id: 'rule', icon: MessageSquare, label: "Ask a Rule", color: "bg-white" },
    { id: 'dispute', icon: Swords, label: "Report Dispute", color: "bg-white" },
    { id: 'house', icon: Pencil, label: "Add House Rule", color: "bg-white" },
  ];

  return (
    <div className="flex flex-col h-screen bg-surface-cream overflow-hidden max-w-md mx-auto relative">
      {/* Top Bar */}
      <header className="h-16 flex items-center justify-between px-6 bg-white/50 backdrop-blur-sm z-30">
        <h1 className="font-display font-bold text-[18px] text-navy-deep">{gameName}</h1>
        
        <div className="flex items-center gap-1.5 px-3 py-1 bg-navy-mid/5 rounded-full border border-navy-mid/10">
          <div className="isometric-container">
            <Clock size={14} className="text-navy-mid" />
          </div>
          <span className="font-sans font-medium text-[14px] text-navy-mid">{timer}</span>
        </div>

        <button 
          onClick={onFinish}
          className="p-2 text-amber-brand hover:bg-amber-brand/5 rounded-lg transition-colors font-sans font-bold text-[14px]"
        >
          Finish
        </button>
      </header>

      {/* Player Turn Tracker */}
      <section className="py-4 overflow-x-auto no-scrollbar flex gap-4 px-6">
        {players.map((player) => (
          <motion.div
            key={player.id}
            layout
            className={`flex-shrink-0 w-[64px] h-[80px] rounded-[12px] flex flex-col items-center justify-center transition-all duration-300 ${
              player.isActive 
                ? 'bg-white shadow-iso-2 border-t-[3px] border-amber-brand' 
                : 'bg-surface-cream border border-navy-deep/5'
            }`}
          >
            <div className="isometric-container mb-1">
              <svg 
                width={player.isActive ? 32 : 24} 
                height={player.isActive ? 32 : 24} 
                viewBox="0 0 24 24" 
                fill="none" 
                xmlns="http://www.w3.org/2000/svg"
              >
                <path 
                  d="M12 2C10.3431 2 9 3.34315 9 5C9 6.65685 10.3431 8 12 8C13.6569 8 15 6.65685 15 5C15 3.34315 13.6569 2 12 2Z" 
                  fill={player.color}
                />
                <path 
                  d="M12 9C9.23858 9 7 11.2386 7 14V16H17V14C17 11.2386 14.7614 9 12 9Z" 
                  fill={player.color}
                />
                <path 
                  d="M6 18C6 17.4477 6.44772 17 7 17H17C17.5523 17 18 17.4477 18 18V20C18 21.1046 17.1046 22 16 22H8C6.89543 22 6 21.1046 6 20V18Z" 
                  fill={player.color}
                />
              </svg>
            </div>
            <span className={`font-sans text-[12px] font-semibold truncate w-full text-center px-1 ${
              player.isActive ? 'text-navy-deep' : 'text-navy-mid/50'
            }`}>
              {player.name}
            </span>
            {player.isActive && (
              <span className="text-amber-brand text-[10px] font-bold uppercase mt-0.5">
                ACTIVE
              </span>
            )}
          </motion.div>
        ))}
      </section>

      {/* Center Content Area */}
      <main className="flex-1 flex flex-col items-center justify-center p-6 relative">
        {/* Isometric Board Illustration */}
        <div className="isometric-container mb-8 scale-110">
          <motion.div 
            className="w-48 h-48 bg-board-beige border-4 border-board-beige-dark isometric-card shadow-iso-3 relative"
            initial={{ rotateX: 35, rotateZ: -45 }}
            animate={{ rotateZ: -40 }}
            transition={{ duration: 10, repeat: Infinity, repeatType: 'reverse' }}
          >
            {/* Board Details */}
            <div className="absolute inset-0 grid grid-cols-4 grid-rows-4 opacity-10">
              {Array.from({ length: 16 }).map((_, i) => (
                <div key={i} className="border border-navy-deep" />
              ))}
            </div>
            {/* Sample Pieces */}
            <div className="absolute top-1/4 left-1/4 w-4 h-4 bg-player-red rounded-full shadow-sm" />
            <div className="absolute bottom-1/3 right-1/4 w-4 h-4 bg-player-blue rounded-full shadow-sm" />
            <div className="absolute top-1/2 right-1/2 w-4 h-4 bg-player-green rounded-full shadow-sm" />
          </motion.div>
        </div>

        {/* Turn Info */}
        <div className="text-center">
          <h2 className="font-display font-bold text-[20px] text-navy-deep">
            It's {activePlayer.name}'s turn
          </h2>
          <p className="font-sans text-[13px] text-navy-mid mt-1">
            Round 3 · Turn 7
          </p>
        </div>
      </main>

      {/* Floating Action Button (FAB) */}
      <div className="absolute bottom-6 right-6 z-50">
        <AnimatePresence>
          {isFabOpen && (
            <div className="absolute bottom-0 right-0">
              {fabOptions.map((option, index) => (
                <motion.div
                  key={option.id}
                  initial={{ opacity: 0, scale: 0, y: 0, x: 0 }}
                  animate={{ 
                    opacity: 1, 
                    scale: 1, 
                    y: -70 * (index + 1),
                    x: -10 * (index)
                  }}
                  exit={{ opacity: 0, scale: 0, y: 0, x: 0 }}
                  transition={{ type: 'spring', damping: 15, stiffness: 200, delay: index * 0.05 }}
                  className="absolute bottom-0 right-0 flex items-center gap-3 pointer-events-auto"
                  onClick={() => {
                    if (option.id === 'rule') {
                      setIsQAOpen(true);
                      setIsFabOpen(false);
                    } else if (option.id === 'dispute') {
                      setIsDisputeOpen(true);
                      setIsFabOpen(false);
                    } else if (option.id === 'house') {
                      setIsHouseRulesOpen(true);
                      setIsFabOpen(false);
                    }
                  }}
                >
                  <span className="bg-navy-deep text-white text-[12px] font-sans font-medium px-3 py-1.5 rounded-full shadow-lg whitespace-nowrap">
                    {option.label}
                  </span>
                  <button className="w-12 h-12 bg-white rounded-full flex items-center justify-center shadow-iso-2 text-navy-deep hover:bg-amber-brand/10 transition-colors border border-navy-deep/5">
                    <option.icon size={20} />
                  </button>
                </motion.div>
              ))}
              
              {/* Animated Lines connecting to FAB (Simplified as background circles or actual SVG lines) */}
              <svg className="absolute inset-0 -z-10 w-[200px] h-[300px] pointer-events-none" style={{ transform: 'translate(-150px, -250px)' }}>
                {/* Lines could be added here for extra polish */}
              </svg>
            </div>
          )}
        </AnimatePresence>

        <motion.button
          whileTap={{ scale: 0.9 }}
          onClick={toggleFab}
          className={`w-16 h-16 rounded-full flex items-center justify-center shadow-iso-3 relative z-10 transition-colors duration-300 ${
            isFabOpen ? 'bg-navy-deep' : 'bg-amber-brand shadow-[0_0_20px_rgba(255,179,0,0.4)]'
          }`}
        >
          <div className="isometric-container">
            {isFabOpen ? (
              <motion.div animate={{ rotate: 90 }}>
                <MoreVertical size={28} className="text-white" />
              </motion.div>
            ) : (
              <HelpCircle size={28} className="text-white" />
            )}
          </div>
        </motion.button>
      </div>

      {/* Rule Q&A Overlay */}
      <RuleQAOverlay 
        isOpen={isQAOpen} 
        onClose={() => setIsQAOpen(false)} 
      />

      {/* Dispute Resolution Screen */}
      <DisputeResolutionScreen 
        isOpen={isDisputeOpen} 
        onClose={() => setIsDisputeOpen(false)} 
        players={players.map(p => ({
          id: p.id,
          name: p.name,
          color: p.color,
          colorClass: p.colorClass
        }))}
      />

      {/* House Rules Manager */}
      <HouseRulesManager 
        isOpen={isHouseRulesOpen} 
        onClose={() => setIsHouseRulesOpen(false)} 
      />
    </div>
  );
};
