import React, { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  X, 
  Mic, 
  Send, 
  BookOpen, 
  AlertTriangle,
  ArrowRight
} from 'lucide-react';
import { askRule } from '../api';

interface Message {
  id: string;
  role: 'user' | 'model';
  text: string;
  citation?: string;
  hasConflict?: boolean;
}

interface RuleQAOverlayProps {
  isOpen: boolean;
  onClose: () => void;
  gameName?: string;
  houseRules?: string[];
}

export const RuleQAOverlay = ({ isOpen, onClose, gameName = "Catan", houseRules = [] }: RuleQAOverlayProps) => {
  const [inputText, setInputText] = useState('');
  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      role: 'user',
      text: "Can I trade resources during someone else's turn?"
    },
    {
      id: '2',
      role: 'model',
      text: "According to the official rules, you can only initiate trades on your own turn. However, you can be the recipient of a trade initiated by the active player.",
      citation: "Official Rule 3.2",
      hasConflict: true
    }
  ]);

  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isOpen]);

  const handleSend = () => {
    if (!inputText.trim()) return;
    
    const newMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      text: inputText
    };
    
    setMessages([...messages, newMessage]);
    const questionText = inputText;
    setInputText('');
    
    askRule(gameName, questionText, houseRules)
      .then((result) => {
        const aiResponse: Message = {
          id: (Date.now() + 1).toString(),
          role: 'model',
          text: result.answer,
          citation: result.citation ?? undefined,
          hasConflict: result.has_house_rule_conflict,
        };
        setMessages(prev => [...prev, aiResponse]);
      })
      .catch(() => {
        setMessages(prev => [...prev, {
          id: (Date.now() + 1).toString(),
          role: 'model',
          text: "Sorry, I couldn't process that question. Please try again.",
        }]);
      });
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-[#1A1F3C]/50 z-[60] backdrop-blur-[2px]"
          />

          {/* Bottom Sheet */}
          <motion.div
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ type: 'spring', damping: 25, stiffness: 200 }}
            className="fixed bottom-0 left-0 right-0 max-w-md mx-auto bg-white z-[70] rounded-t-[28px] flex flex-col h-[85vh] shadow-2xl"
          >
            {/* Drag Handle */}
            <div className="flex justify-center py-3">
              <div className="w-8 h-1 bg-navy-mid/30 rounded-full" />
            </div>

            {/* Header */}
            <div className="flex items-center justify-between px-6 pb-4 border-b border-navy-deep/5">
              <h2 className="font-display font-bold text-[18px] text-navy-deep">Ask a Rule</h2>
              <button 
                onClick={onClose}
                className="p-2 text-navy-mid hover:bg-navy-deep/5 rounded-full transition-colors"
              >
                <X size={20} />
              </button>
            </div>

            {/* Chat History */}
            <div 
              ref={scrollRef}
              className="flex-1 overflow-y-auto p-6 space-y-6 no-scrollbar"
            >
              {messages.map((msg) => (
                <div 
                  key={msg.id}
                  className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}
                >
                  <div className={`max-w-[85%] p-4 rounded-[16px] text-[14px] font-sans leading-relaxed ${
                    msg.role === 'user' 
                      ? 'bg-amber-brand text-white rounded-br-none' 
                      : 'bg-white text-navy-deep shadow-iso-1 border border-navy-deep/5 rounded-bl-none'
                  }`}>
                    {msg.text}
                  </div>

                  {msg.role === 'model' && msg.citation && (
                    <div className="mt-2 flex items-center gap-1.5 px-1">
                      <div className="isometric-container">
                        <BookOpen size={12} className="text-navy-mid" />
                      </div>
                      <span className="text-[11px] font-sans text-navy-mid">
                        {msg.citation}
                      </span>
                    </div>
                  )}

                  {msg.role === 'model' && msg.hasConflict && (
                    <div className="mt-4 w-full">
                      <div className="bg-amber-light/30 border border-amber-brand/20 rounded-xl p-3">
                        <div className="flex items-center gap-2 text-amber-dark mb-3">
                          <div className="isometric-container">
                            <AlertTriangle size={14} />
                          </div>
                          <span className="text-[12px] font-sans font-bold">
                            Your house rule modifies this
                          </span>
                        </div>
                        <div className="flex gap-2">
                          <button className="flex-1 bg-white text-amber-dark text-[11px] font-bold py-2 rounded-full shadow-sm border border-amber-brand/10 hover:bg-amber-brand/5 transition-colors">
                            Apply House Rule
                          </button>
                          <button className="flex-1 bg-amber-brand text-white text-[11px] font-bold py-2 rounded-full shadow-sm hover:bg-amber-dark transition-colors">
                            Use Official
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* Input Area */}
            <div className="p-4 bg-white border-t border-navy-deep/5 sticky bottom-0">
              <div className="flex items-center gap-3">
                <button className="w-12 h-12 bg-navy-deep text-white rounded-full flex items-center justify-center shadow-lg active:scale-95 transition-transform">
                  <Mic size={20} />
                </button>
                
                <div className="flex-1 flex items-center bg-surface-cream rounded-[12px] px-4 h-12 border border-navy-deep/5">
                  <input 
                    type="text" 
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    onKeyPress={(e) => e.key === 'Enter' && handleSend()}
                    placeholder="Ask about a rule..."
                    className="flex-1 bg-transparent border-none focus:ring-0 text-[14px] font-sans text-navy-deep placeholder:text-navy-mid/50"
                  />
                </div>

                <button 
                  onClick={handleSend}
                  disabled={!inputText.trim()}
                  className={`w-12 h-12 rounded-full flex items-center justify-center shadow-lg transition-all active:scale-95 ${
                    inputText.trim() ? 'bg-amber-brand text-white' : 'bg-navy-mid/20 text-white'
                  }`}
                >
                  <div className="isometric-container">
                    <ArrowRight size={20} />
                  </div>
                </button>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
};
