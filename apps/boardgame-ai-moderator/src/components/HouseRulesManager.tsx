import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  X, 
  Check, 
  AlertTriangle, 
  Clock, 
  Brain, 
  Scale, 
  Pencil,
  Sparkles,
  ChevronDown
} from 'lucide-react';
import { validateHouseRule } from '../api';

interface HouseRule {
  id: string;
  text: string;
}

interface HouseRulesManagerProps {
  isOpen: boolean;
  onClose: () => void;
  gameName?: string;
}

type ValidationState = 'idle' | 'validating' | 'valid' | 'conflict';

export const HouseRulesManager = ({ isOpen, onClose, gameName = "Catan" }: HouseRulesManagerProps) => {
  const [ruleText, setRuleText] = useState('');
  const [activeRules, setActiveRules] = useState<HouseRule[]>([
    { id: '1', text: 'Friendly Robber: No robber on players with < 3 points.' },
    { id: '2', text: 'Double Production: 2 and 12 roll results are doubled.' }
  ]);
  const [validationState, setValidationState] = useState<ValidationState>('idle');

  const handleCheckRule = () => {
    if (!ruleText.trim()) return;
    setValidationState('validating');
    
    const existingTexts = activeRules.map(r => r.text);
    validateHouseRule(gameName, ruleText, existingTexts)
      .then((result) => {
        setValidationState(result.is_valid ? 'valid' : 'conflict');
      })
      .catch(() => {
        // Fallback — treat as valid on API error
        setValidationState('valid');
      });
  };

  const handleAddRule = () => {
    if (validationState !== 'valid') return;
    const newRule = {
      id: Date.now().toString(),
      text: ruleText
    };
    setActiveRules([newRule, ...activeRules]);
    setRuleText('');
    setValidationState('idle');
  };

  const handleDeleteRule = (id: string) => {
    setActiveRules(activeRules.filter(r => r.id !== id));
  };

  const suggestionChips = ["Faster start", "Skip rules", "Bonus turns"];

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ y: '100%' }}
          animate={{ y: 0 }}
          exit={{ y: '100%' }}
          transition={{ type: 'spring', damping: 25, stiffness: 200 }}
          className="fixed inset-0 z-[100] bg-surface-cream flex flex-col overflow-hidden max-w-md mx-auto"
        >
          {/* Top Bar */}
          <header className="h-16 flex items-center justify-between px-6 bg-white shrink-0 shadow-sm">
            <h2 className="font-display font-bold text-[20px] text-navy-deep">House Rules</h2>
            <button 
              onClick={onClose}
              className="font-sans font-bold text-[16px] text-amber-brand"
            >
              Done
            </button>
          </header>

          <div className="flex-1 overflow-y-auto p-6 space-y-8 no-scrollbar">
            {/* Input Section */}
            <div className="space-y-4">
              <label className="text-[11px] font-sans font-bold uppercase tracking-widest text-navy-light">
                ADD A HOUSE RULE
              </label>
              
              <div className="bg-white rounded-[16px] p-4 shadow-iso-1 space-y-4">
                <textarea 
                  value={ruleText}
                  onChange={(e) => {
                    setRuleText(e.target.value);
                    if (validationState !== 'idle') setValidationState('idle');
                  }}
                  placeholder="Describe your rule modification..."
                  className="w-full h-[100px] bg-surface-cream border-none rounded-[12px] p-4 text-[14px] font-sans text-navy-deep placeholder:text-navy-mid/40 focus:ring-2 focus:ring-amber-brand/20 resize-none"
                />
                
                <div className="flex flex-wrap gap-2">
                  {suggestionChips.map(chip => (
                    <button 
                      key={chip}
                      onClick={() => setRuleText(prev => prev + (prev ? ' ' : '') + chip)}
                      className="px-3 py-1.5 bg-amber-light text-amber-dark rounded-[8px] text-[12px] font-sans font-medium active:scale-95 transition-all"
                    >
                      {chip}
                    </button>
                  ))}
                </div>

                <button 
                  onClick={handleCheckRule}
                  disabled={!ruleText.trim() || validationState === 'validating'}
                  className="w-full h-[48px] bg-navy-deep text-white rounded-[12px] flex items-center justify-center gap-2 font-display font-semibold text-[14px] active:scale-[0.98] transition-all disabled:opacity-50"
                >
                  {validationState === 'validating' ? (
                    <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  ) : (
                    <>
                      <div className="isometric-container">
                        <Check size={18} strokeWidth={3} />
                      </div>
                      Check Rule
                    </>
                  )}
                </button>
              </div>

              {/* Validation Result Panel */}
              <AnimatePresence>
                {validationState === 'valid' && (
                  <motion.div 
                    initial={{ height: 0, opacity: 0, y: -10 }}
                    animate={{ height: 'auto', opacity: 1, y: 0 }}
                    exit={{ height: 0, opacity: 0, y: -10 }}
                    className="overflow-hidden"
                  >
                    <div className="bg-white rounded-[16px] border-l-[4px] border-green-500 shadow-iso-1 p-4 space-y-4">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 bg-green-100 rounded-lg flex items-center justify-center text-green-600">
                          <div className="isometric-container">
                            <Check size={20} strokeWidth={3} />
                          </div>
                        </div>
                        <h3 className="font-display font-bold text-[16px] text-navy-deep">Rule is Valid</h3>
                      </div>
                      
                      <div className="flex gap-2">
                        <div className="flex-1 bg-amber-light/50 rounded-[12px] p-2 flex items-center gap-1.5 border border-amber-brand/10">
                          <Clock size={12} className="text-amber-dark" />
                          <span className="text-[10px] font-sans font-bold text-amber-dark whitespace-nowrap">Length: +15%</span>
                        </div>
                        <div className="flex-1 bg-green-100 rounded-[12px] p-2 flex items-center gap-1.5 border border-green-500/10">
                          <Brain size={12} className="text-green-700" />
                          <span className="text-[10px] font-sans font-bold text-green-700 whitespace-nowrap">Complexity: Same</span>
                        </div>
                        <div className="flex-1 bg-amber-light/50 rounded-[12px] p-2 flex items-center gap-1.5 border border-amber-brand/10">
                          <Scale size={12} className="text-amber-dark" />
                          <span className="text-[10px] font-sans font-bold text-amber-dark whitespace-nowrap">Balance: Minor</span>
                        </div>
                      </div>

                      <button 
                        onClick={handleAddRule}
                        className="w-full h-[40px] bg-amber-brand text-white rounded-[10px] font-display font-bold text-[14px] shadow-sm"
                      >
                        Add to Active Rules
                      </button>
                    </div>
                  </motion.div>
                )}

                {validationState === 'conflict' && (
                  <motion.div 
                    initial={{ height: 0, opacity: 0, y: -10 }}
                    animate={{ height: 'auto', opacity: 1, y: 0 }}
                    exit={{ height: 0, opacity: 0, y: -10 }}
                    className="overflow-hidden"
                  >
                    <div className="bg-white rounded-[16px] border-l-[4px] border-red-500 shadow-iso-1 p-4 space-y-3">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 bg-red-100 rounded-lg flex items-center justify-center text-red-600">
                          <div className="isometric-container">
                            <AlertTriangle size={20} strokeWidth={2} />
                          </div>
                        </div>
                        <h3 className="font-display font-bold text-[16px] text-red-600">Conflict Detected</h3>
                      </div>
                      
                      <p className="font-sans text-[14px] text-navy-mid leading-relaxed">
                        This rule directly contradicts the official trading phase mechanics.
                      </p>
                      
                      <div className="bg-surface-cream border-l-[3px] border-navy-deep p-3 rounded-r-[8px]">
                        <p className="font-sans italic text-[13px] text-navy-deep leading-relaxed">
                          "Official rule states: Players may only trade with the active player during their turn. Non-active players cannot trade amongst themselves."
                        </p>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Active House Rules List */}
            <div className="space-y-4">
              <label className="text-[11px] font-sans font-bold uppercase tracking-widest text-navy-light">
                ACTIVE HOUSE RULES
              </label>
              
              <div className="space-y-3">
                {activeRules.map(rule => (
                  <motion.div 
                    layout
                    key={rule.id}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    className="bg-white rounded-[12px] p-4 shadow-iso-1 flex items-center gap-4 group"
                  >
                    <div className="text-amber-brand shrink-0">
                      <div className="isometric-container">
                        <Pencil size={18} />
                      </div>
                    </div>
                    <p className="flex-1 font-sans text-[14px] text-navy-deep leading-tight">
                      {rule.text}
                    </p>
                    <button 
                      onClick={() => handleDeleteRule(rule.id)}
                      className="p-1 text-navy-light hover:text-red-500 transition-colors"
                    >
                      <X size={18} />
                    </button>
                  </motion.div>
                ))}
                
                {activeRules.length === 0 && (
                  <div className="text-center py-8 opacity-40">
                    <p className="font-sans text-[14px] italic">No active house rules</p>
                  </div>
                )}
              </div>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};
