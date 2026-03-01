import React, { useState, useEffect } from 'react';
import { motion } from 'motion/react';
import { 
  ArrowLeft, 
  SlidersHorizontal, 
  Mic,
  Users,
  Loader2
} from 'lucide-react';
import { searchGames, type SearchResult } from '../api';
import { localSearch } from '../gameDatabase';

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

interface ResultCardProps {
  name: string;
  players: string;
  complexity: number;
  onClick: () => void;
  key?: React.Key;
}

const ResultCard = ({ name, players, complexity, onClick }: ResultCardProps) => (
  <motion.div 
    onClick={onClick}
    whileTap={{ scale: 1.02, y: -4, boxShadow: "var(--shadow-iso-2)" }}
    transition={{ type: "spring", stiffness: 400, damping: 17 }}
    className="bg-white rounded-[16px] shadow-iso-1 overflow-hidden flex flex-col cursor-pointer border border-navy-deep/5"
  >
    <div className="h-[120px] bg-amber-light flex items-center justify-center p-4">
      <div className="isometric-container">
        <div className="w-14 h-14 bg-board-beige border-2 border-board-beige-dark isometric-card shadow-sm">
          <div className="absolute top-2 left-2 w-2 h-4 bg-player-red rounded-t-full" style={{ transform: 'translateZ(4px)' }} />
          <div className="absolute bottom-2 right-2 w-2 h-4 bg-player-blue rounded-t-full" style={{ transform: 'translateZ(4px)' }} />
        </div>
      </div>
    </div>
    <div className="p-3 flex flex-col justify-between flex-1">
      <div>
        <h4 className="font-display font-bold text-[14px] text-navy-deep leading-tight">{name}</h4>
        <div className="flex items-center gap-1 mt-1">
          <Users size={10} className="text-navy-mid" />
          <span className="text-[12px] font-sans text-navy-mid">{players}</span>
        </div>
      </div>
      <div className="flex gap-1 mt-2">
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

interface SearchScreenProps {
  onBack: () => void;
  onSelectGame: (gameName: string) => void;
  searchQuery?: string;
}

export const SearchScreen = ({ onBack, onSelectGame, searchQuery = "" }: SearchScreenProps) => {
  const [query, setQuery] = useState(searchQuery);
  const [results, setResults] = useState<{ name: string; players: string; complexity: number }[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  // Instant local search + optional API enrichment
  useEffect(() => {
    // Immediately show local results
    const local = localSearch(query).map((g) => ({
      name: g.name,
      players: g.playerCount,
      complexity: g.complexity,
    }));
    setResults(local);

    // If query is non-empty, also try the API for potentially richer results
    if (!query.trim()) return;

    const timer = setTimeout(() => {
      setIsLoading(true);
      searchGames(query)
        .then((apiResults) => {
          if (apiResults.length > 0) {
            // Merge: API results first (deduplicated), then local-only
            const apiMapped = apiResults.map((r) => ({
              name: r.name,
              players: r.player_count,
              complexity: r.complexity,
            }));
            const apiNames = new Set(apiMapped.map((r) => r.name.toLowerCase()));
            const localOnly = local.filter((r) => !apiNames.has(r.name.toLowerCase()));
            setResults([...apiMapped, ...localOnly]);
          }
        })
        .catch(() => {
          // Keep local results — already shown
        })
        .finally(() => setIsLoading(false));
    }, 500);
    return () => clearTimeout(timer);
  }, [query]);

  return (
    <div className="flex flex-col h-screen bg-surface-cream overflow-hidden max-w-md mx-auto relative">
      {/* Top Bar */}
      <header className="h-16 bg-white flex items-center justify-between px-6 z-20 shadow-sm shrink-0">
        <button onClick={onBack} className="p-2 -ml-2 text-navy-deep hover:bg-surface-cream rounded-full transition-colors">
          <ArrowLeft size={24} />
        </button>
        <h1 className="font-display font-bold text-[20px] text-navy-deep">Find a Game</h1>
        <button className="p-2 -mr-2 text-navy-deep hover:bg-surface-cream rounded-full transition-colors">
          <SlidersHorizontal size={20} />
        </button>
      </header>

      {/* Persistent Search Bar */}
      <div className="px-6 py-4 bg-white/50 backdrop-blur-sm z-10">
        <div className="h-14 bg-white rounded-[16px] border-[1.5px] border-navy-deep/10 shadow-iso-1 flex items-center px-4 gap-3">
          <IsometricBoardIcon size={20} color="#8C93B8" />
          <input 
            type="text" 
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search for a game..." 
            className="flex-1 bg-transparent border-none focus:ring-0 text-[15px] placeholder:text-navy-light text-navy-deep"
          />
          <button className="text-amber-brand p-1">
            <Mic size={20} />
          </button>
        </div>
      </div>

      {/* Results Grid */}
      <main className="flex-1 overflow-y-auto px-6 pb-8">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 size={32} className="animate-spin text-amber-brand" />
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4 mt-2">
            {results.map((game, i) => (
              <ResultCard key={i} name={game.name} players={game.players} complexity={game.complexity} onClick={() => onSelectGame(game.name)} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
};
