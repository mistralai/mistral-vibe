import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  X, 
  HelpCircle, 
  BookOpen, 
  Check, 
  AlertCircle,
  ChevronRight,
  ArrowDown
} from 'lucide-react';

interface Player {
  id: number;
  name: string;
  color: string;
  colorClass: string;
}

interface DisputeResolutionScreenProps {
  isOpen: boolean;
  onClose: () => void;
  players: Player[];
}

const CITATION_LAYERS = [
  { id: 'official', label: 'Official Rule', color: 'bg-amber-brand', textColor: 'text-white' },
  { id: 'faq', label: 'FAQ / Errata', color: 'bg-blue-400', textColor: 'text-white' },
  { id: 'community', label: 'Community Consensus', color: 'bg-green-400', textColor: 'text-white' },
  { id: 'house', label: 'Your House Rules', color: 'bg-purple-400', textColor: 'text-white' },
];

export const DisputeResolutionScreen = ({ isOpen, onClose, players }: DisputeResolutionScreenProps) => {
  const [disputeText, setDisputeText] = useState('');
  const [selectedPlayers, setSelectedPlayers] = useState<number[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [ruling, setRuling] = useState<{
    title: string;
    body: string;
    source: string;
  } | null>(null);

  const togglePlayer = (id: number) => {
    setSelectedPlayers(prev => 
      prev.includes(id) ? prev.filter(p => p !== id) : [...prev, id]
    );
  };

  const handleGetRuling = () => {
    if (!disputeText.trim()) return;
    
    setIsProcessing(true);
    // Simulate processing
    setTimeout(() => {
      setRuling({
        title: "Trade Timing Restriction",
        body: "Official rules for Catan state that the active player may only trade with other players. Non-active players cannot trade amongst themselves. Since Bob is not the active player, he cannot initiate a trade with Alice.",
        source: 'official'
      });
      setIsProcessing(false);
    }, 1500);
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ y: '100%' }}
          animate={{ y: 0 }}
          exit={{ y: '100%' }}
          transition={{ type: 'spring', damping: 25, stiffness: 200 }}
          className="fixed inset-0 z-[100] bg-white flex flex-col overflow-hidden rounded-t-[28px] shadow-2xl max-w-md mx-auto"
        >
          {/* Header / Close */}
          <div className="absolute top-4 right-4 z-10">
            <button 
              onClick={onClose}
              className="p-2 bg-navy-deep/5 text-navy-deep rounded-full hover:bg-navy-deep/10 transition-colors"
            >
              <X size={20} />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto no-scrollbar pb-24">
            {/* Top Area: Isometric Illustration */}
            <div className="h-[200px] bg-amber-light/30 flex items-center justify-center relative overflow-hidden">
              <div className="absolute inset-0 opacity-10">
                <div className="absolute inset-0 grid grid-cols-6 grid-rows-4 border border-amber-brand/20">
                  {Array.from({ length: 24 }).map((_, i) => (
                    <div key={i} className="border border-amber-brand/20" />
                  ))}
                </div>
              </div>
              
              <div className="isometric-container relative">
                {/* Two game pieces facing each other */}
                <motion.div 
                  initial={{ x: -20, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  className="absolute -left-12 -top-4 w-12 h-12 bg-player-red rounded-full shadow-iso-2 border-2 border-white/20" 
                />
                <motion.div 
                  initial={{ x: 20, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  className="absolute left-4 -top-4 w-12 h-12 bg-player-blue rounded-full shadow-iso-2 border-2 border-white/20" 
                />
                
                {/* Floating Question Mark */}
                <motion.div
                  animate={{ y: [0, -10, 0] }}
                  transition={{ repeat: Infinity, duration: 2 }}
                  className="absolute -left-2 -top-20 text-amber-brand"
                >
                  <HelpCircle size={48} strokeWidth={2.5} />
                </motion.div>
              </div>
            </div>

            <div className="p-6 space-y-8">
              <div className="text-center">
                <h1 className="font-display font-extrabold text-[24px] text-navy-deep">Resolve a Dispute</h1>
              </div>

              {/* Dispute Input Section */}
              <div className="space-y-3">
                <label className="text-[11px] font-sans font-bold uppercase tracking-widest text-navy-light">
                  WHAT HAPPENED?
                </label>
                <textarea 
                  value={disputeText}
                  onChange={(e) => setDisputeText(e.target.value)}
                  placeholder="Describe the disputed action..."
                  className="w-full h-[120px] bg-surface-cream border-[1.5px] border-navy-deep/10 rounded-[12px] p-4 text-[14px] font-sans text-navy-deep focus:ring-amber-brand focus:border-amber-brand transition-all resize-none"
                />
              </div>

              {/* Players Involved Selector */}
              <div className="space-y-3">
                <label className="text-[11px] font-sans font-bold uppercase tracking-widest text-navy-light">
                  PLAYERS INVOLVED
                </label>
                <div className="flex flex-wrap gap-3">
                  {players.map((player) => {
                    const isSelected = selectedPlayers.includes(player.id);
                    return (
                      <button
                        key={player.id}
                        onClick={() => togglePlayer(player.id)}
                        className={`flex items-center gap-2 px-4 py-2.5 rounded-full transition-all ${
                          isSelected 
                            ? `bg-white shadow-iso-1 border-2 border-player-${player.color}` 
                            : 'bg-surface-cream border-2 border-transparent'
                        }`}
                      >
                        <div className={`w-3 h-3 rounded-full bg-player-${player.color}`} />
                        <span className={`text-[13px] font-sans font-bold ${isSelected ? 'text-navy-deep' : 'text-navy-mid'}`}>
                          {player.name}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Get Ruling CTA */}
              {!ruling && (
                <button 
                  onClick={handleGetRuling}
                  disabled={!disputeText.trim() || isProcessing}
                  className={`w-full h-[56px] rounded-[16px] font-display font-bold text-[16px] flex items-center justify-center transition-all active:scale-95 ${
                    isProcessing ? 'bg-navy-mid text-white' : 'bg-navy-deep text-white shadow-iso-2 hover:bg-navy-mid'
                  }`}
                >
                  {isProcessing ? (
                    <div className="flex items-center gap-2">
                      <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                      <span>CONSULTING RULES...</span>
                    </div>
                  ) : (
                    "Get Ruling"
                  )}
                </button>
              )}

              {/* Ruling Result */}
              {ruling && (
                <motion.div 
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="space-y-6"
                >
                  <div className="bg-white rounded-[16px] shadow-iso-2 border-l-[4px] border-amber-brand p-5 space-y-4">
                    <div className="flex items-center gap-3 text-amber-brand">
                      <div className="isometric-container">
                        <BookOpen size={32} />
                      </div>
                      <span className="text-[12px] font-sans font-bold uppercase tracking-widest">OFFICIAL RULING</span>
                    </div>
                    
                    <div className="space-y-2">
                      <h3 className="font-display font-bold text-[16px] text-navy-deep">{ruling.title}</h3>
                      <p className="font-sans text-[14px] leading-relaxed text-navy-mid">
                        {ruling.body}
                      </p>
                    </div>
                  </div>

                  {/* Citation Hierarchy */}
                  <div className="space-y-4 pt-4">
                    <label className="text-[11px] font-sans font-bold uppercase tracking-widest text-navy-light block text-center">
                      CITATION HIERARCHY
                    </label>
                    
                    <div className="flex flex-col items-center py-4">
                      <div className="relative w-full max-w-[240px] isometric-container flex flex-col items-center">
                        {CITATION_LAYERS.map((layer, index) => (
                          <div 
                            key={layer.id}
                            className={`relative w-full h-10 ${layer.color} ${layer.textColor} flex items-center justify-center font-sans font-bold text-[11px] uppercase tracking-wider shadow-iso-1 border border-white/10`}
                            style={{ 
                              zIndex: CITATION_LAYERS.length - index,
                              transform: `translateY(${index * -4}px) rotateX(0deg) rotateZ(0deg)`,
                              marginTop: index === 0 ? 0 : '-8px'
                            }}
                          >
                            {layer.label}
                            
                            {ruling.source === layer.id && (
                              <motion.div 
                                initial={{ x: 20, opacity: 0 }}
                                animate={{ x: 0, opacity: 1 }}
                                className="absolute -left-12 flex items-center gap-2 text-amber-brand"
                              >
                                <ChevronRight size={24} strokeWidth={3} />
                              </motion.div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </motion.div>
              )}
            </div>
          </div>

          {/* Bottom Row Actions */}
          {ruling && (
            <div className="absolute bottom-0 left-0 right-0 p-6 bg-white border-t border-navy-deep/5 flex gap-4">
              <button 
                onClick={onClose}
                className="flex-1 h-[56px] bg-amber-brand text-white font-display font-bold text-[16px] rounded-[16px] shadow-iso-2 active:scale-95 transition-all"
              >
                Accept Ruling
              </button>
              <button 
                onClick={onClose}
                className="flex-1 h-[56px] border-2 border-navy-deep text-navy-deep font-display font-bold text-[16px] rounded-[16px] active:scale-95 transition-all"
              >
                Override & Continue
              </button>
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
};
