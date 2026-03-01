import React from 'react';
import { motion } from 'motion/react';
import { 
  Search, 
  Camera, 
  Mic, 
  Home, 
  Layers, 
  Gamepad2, 
  User,
  ChevronRight,
  Users
} from 'lucide-react';

const IsometricBoardIcon = ({ size = 32, color = "currentColor" }: { size?: number, color?: string }) => (
  <div className="relative" style={{ width: size, height: size, perspective: '1000px' }}>
    <div 
      className="absolute inset-0 bg-board-beige border border-board-beige-dark shadow-sm"
      style={{ 
        transform: 'rotateX(45deg) rotateZ(-45deg)',
        transformStyle: 'preserve-3d',
        backgroundColor: color === "currentColor" ? undefined : color
      }}
    >
      <div className="absolute inset-0 grid grid-cols-2 grid-rows-2 opacity-20">
        <div className="border border-navy-deep" />
        <div className="border border-navy-deep" />
        <div className="border border-navy-deep" />
        <div className="border border-navy-deep" />
      </div>
    </div>
  </div>
);

const GameCard = ({ name, players, complexity }: { name: string, players: string, complexity: number }) => (
  <motion.div 
    whileHover={{ y: -5 }}
    className="min-w-[160px] h-[200px] bg-white rounded-[16px] shadow-iso-1 overflow-hidden flex flex-col"
  >
    <div className="h-[60%] bg-amber-light flex items-center justify-center p-4">
      <div className="isometric-container">
        <div className="w-16 h-16 bg-board-beige border-2 border-board-beige-dark isometric-card shadow-sm">
          <div className="absolute top-2 left-2 w-2 h-4 bg-player-red rounded-t-full" style={{ transform: 'translateZ(4px)' }} />
          <div className="absolute bottom-2 right-2 w-2 h-4 bg-player-blue rounded-t-full" style={{ transform: 'translateZ(4px)' }} />
        </div>
      </div>
    </div>
    <div className="h-[40%] p-3 flex flex-col justify-between">
      <div>
        <h4 className="font-display font-bold text-[14px] text-navy-deep leading-tight">{name}</h4>
        <div className="flex items-center gap-1 mt-1">
          <Users size={10} className="text-navy-mid" />
          <span className="text-[12px] font-sans text-navy-mid">{players}</span>
        </div>
      </div>
      <div className="flex gap-1">
        {[0, 1, 2].map((i) => (
          <div 
            key={i} 
            className={`w-2 h-2 rotate-45 ${i < complexity ? 'bg-amber-brand' : 'bg-navy-light/20'}`} 
          />
        ))}
      </div>
    </div>
  </motion.div>
);

interface HomeScreenProps {
  onSearch: () => void;
}

export const HomeScreen = ({ onSearch }: HomeScreenProps) => {
  return (
    <div className="flex flex-col h-screen bg-surface-cream overflow-hidden max-w-md mx-auto relative">
      {/* Top Navigation Bar */}
      <header className="h-16 bg-white flex items-center justify-between px-6 z-20 shadow-sm shrink-0">
        <div className="flex items-center gap-3">
          <IsometricBoardIcon size={32} />
          <span className="font-display font-bold text-[18px] text-navy-deep tracking-tight">BOARDSAGE</span>
        </div>
        <div className="w-9 h-9 rounded-full bg-amber-brand flex items-center justify-center text-white font-bold text-sm shadow-sm">
          E
        </div>
      </header>

      {/* Main Scrollable Content */}
      <main className="flex-1 overflow-y-auto pb-24">
        {/* Hero Section */}
        <section className="bg-amber-light rounded-b-[32px] p-6 pt-8 relative overflow-hidden min-h-[320px]">
          {/* Isometric Table Illustration (Bleeds right) */}
          <div className="absolute right-[-40px] top-10 w-[240px] h-[240px] opacity-40 pointer-events-none">
            <div className="isometric-container">
              <div className="w-48 h-48 bg-board-beige-dark/20 rounded-full blur-3xl absolute top-10" />
              <div className="w-40 h-40 bg-board-beige border-4 border-board-beige-dark isometric-card shadow-iso-2">
                <div className="absolute top-1/2 left-1/2 w-4 h-4 bg-white rotate-45 shadow-sm" />
                <div className="absolute top-1/4 right-1/4 w-8 h-12 bg-white rounded-sm shadow-sm" />
              </div>
            </div>
          </div>

          <div className="relative z-10 max-w-[240px]">
            <span className="inline-block bg-amber-brand text-white font-sans font-semibold text-[10px] uppercase tracking-wider px-2 py-1 rounded-[8px] mb-4">
              AI POWERED
            </span>
            <h2 className="font-display font-extrabold text-[28px] text-navy-deep leading-[1.1] mb-3">
              Learn any board game in minutes
            </h2>
            <p className="font-sans text-[14px] text-navy-mid leading-relaxed mb-8">
              Upload your rulebook or search by name
            </p>
          </div>

          {/* Primary CTAs */}
          <div className="flex gap-3 relative z-10">
            <button 
              onClick={onSearch}
              className="flex-1 h-12 bg-navy-deep text-white rounded-[12px] flex items-center justify-center gap-2 font-sans font-semibold text-[14px] shadow-iso-1 active:scale-95 transition-transform"
            >
              <Search size={16} />
              Search Game
            </button>
            <button className="flex-1 h-12 bg-amber-brand text-white rounded-[12px] flex items-center justify-center gap-2 font-sans font-semibold text-[14px] shadow-iso-1 active:scale-95 transition-transform">
              <Camera size={16} />
              Upload Rules
            </button>
          </div>
        </section>

        {/* Search Bar */}
        <div className="px-6 mt-6">
          <div className="h-14 bg-white rounded-[16px] border-[1.5px] border-navy-deep/10 shadow-iso-1 flex items-center px-4 gap-3">
            <IsometricBoardIcon size={20} color="#8C93B8" />
            <input 
              type="text" 
              placeholder="Search for a game..." 
              className="flex-1 bg-transparent border-none focus:ring-0 text-[15px] placeholder:text-navy-light text-navy-deep"
            />
            <button className="text-amber-brand p-1">
              <Mic size={20} />
            </button>
          </div>
        </div>

        {/* Popular Games Section */}
        <section className="mt-8">
          <div className="px-6 mb-4">
            <h3 className="text-[11px] font-sans font-semibold text-navy-light uppercase tracking-[0.08em]">
              POPULAR GAMES
            </h3>
          </div>
          <div className="flex gap-4 overflow-x-auto px-6 pb-4 scrollbar-hide">
            <GameCard name="Catan" players="3-4 players" complexity={2} />
            <GameCard name="Chess" players="2 players" complexity={3} />
            <GameCard name="Monopoly" players="2-6 players" complexity={1} />
            <GameCard name="Wingspan" players="1-5 players" complexity={2} />
          </div>
        </section>

        {/* Recent Games Section */}
        <section className="mt-6 px-6">
          <div className="mb-4">
            <h3 className="text-[11px] font-sans font-semibold text-navy-light uppercase tracking-[0.08em]">
              RECENTLY PLAYED
            </h3>
          </div>
          <div className="space-y-3">
            {[
              { name: 'Catan', date: 'Played 2h ago' },
              { name: 'Ticket to Ride', date: 'Played yesterday' }
            ].map((game, i) => (
              <div key={i} className="h-[72px] bg-white rounded-[16px] shadow-sm flex items-center p-3 gap-4 border border-navy-deep/5">
                <div className="w-12 h-12 bg-amber-light rounded-[12px] flex items-center justify-center shrink-0">
                  <IsometricBoardIcon size={24} />
                </div>
                <div className="flex-1">
                  <h4 className="font-display font-bold text-[14px] text-navy-deep">{game.name}</h4>
                  <p className="font-sans text-[12px] text-navy-light">{game.date}</p>
                </div>
                <button className="h-8 px-4 bg-amber-brand text-white text-[12px] font-bold rounded-[12px] shadow-sm active:scale-95 transition-transform">
                  Continue
                </button>
              </div>
            ))}
          </div>
        </section>
      </main>

      {/* Bottom Navigation Bar */}
      <nav className="h-[72px] bg-white border-t border-navy-deep/5 flex items-center justify-around px-4 absolute bottom-0 left-0 right-0 z-30">
        <div className="flex flex-col items-center gap-1 text-amber-brand">
          <Home size={24} />
          <span className="text-[10px] font-sans font-bold uppercase tracking-wider">Home</span>
        </div>
        <div className="flex flex-col items-center gap-1 text-navy-light">
          <Layers size={24} />
        </div>
        <div className="flex flex-col items-center gap-1 text-navy-light">
          <Gamepad2 size={24} />
        </div>
        <div className="flex flex-col items-center gap-1 text-navy-light">
          <User size={24} />
        </div>
      </nav>
    </div>
  );
};
