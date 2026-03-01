import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { 
  Users, 
  Play, 
  RotateCcw, 
  MessageSquare, 
  Mic, 
  MicOff, 
  Trophy, 
  ChevronRight,
  Plus,
  Trash2,
  Settings
} from 'lucide-react';
import { Player, PlayerColor, GameState, ChatMessage } from './types';
import { askRule } from './api';
import { SplashScreen } from './components/SplashScreen';
import { HomeScreen } from './components/HomeScreen';
import { SearchScreen } from './components/SearchScreen';
import { GameDetailScreen } from './components/GameDetailScreen';
import { QuickStartScreen } from './components/QuickStartScreen';
import { StepByStepScreen } from './components/StepByStepScreen';
import { SimulationScreen } from './components/SimulationScreen';
import { ActiveSessionScreen } from './components/ActiveSessionScreen';
import { PostGameSummary } from './components/PostGameSummary';

const PLAYER_COLORS = {
  [PlayerColor.RED]: 'bg-player-red',
  [PlayerColor.BLUE]: 'bg-player-blue',
  [PlayerColor.YELLOW]: 'bg-player-yellow',
  [PlayerColor.GREEN]: 'bg-player-green',
};

export default function App() {
  const [screen, setScreen] = useState<'splash' | 'home' | 'search' | 'detail' | 'moderator' | 'quickstart' | 'stepbystep' | 'simulation' | 'summary'>('splash');
  const [selectedGame, setSelectedGame] = useState<string>('Catan');
  const [gameState, setGameState] = useState<GameState>({
    gameName: 'Catan',
    players: [
      { id: '1', name: 'Alice', color: PlayerColor.RED, score: 0, isCurrentTurn: true },
      { id: '2', name: 'Bob', color: PlayerColor.BLUE, score: 0, isCurrentTurn: false },
    ],
    turnCount: 1,
    status: 'setup',
    history: [],
  });

  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: 'model', text: "Hello! I'm your AI Board Game Moderator. Tell me what game you're playing and I'll help you manage the rules and turns.", timestamp: Date.now() }
  ]);
  const [inputText, setInputText] = useState('');
  const [isRecording, setIsRecording] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setScreen('home');
    }, 3000);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSendMessage = async (text: string) => {
    if (!text.trim()) return;

    const newUserMessage: ChatMessage = { role: 'user', text, timestamp: Date.now() };
    setMessages(prev => [...prev, newUserMessage]);
    setInputText('');

    try {
      const result = await askRule(selectedGame, text);
      const modelResponse: ChatMessage = { 
        role: 'model', 
        text: result.answer, 
        timestamp: Date.now() 
      };
      setMessages(prev => [...prev, modelResponse]);
    } catch (error) {
      console.error("AI Error:", error);
      setMessages(prev => [...prev, { role: 'system', text: "Sorry, I encountered an error connecting to the AI.", timestamp: Date.now() }]);
    }
  };

  const nextTurn = () => {
    setGameState(prev => {
      const currentIndex = prev.players.findIndex(p => p.isCurrentTurn);
      const nextIndex = (currentIndex + 1) % prev.players.length;
      const newPlayers = prev.players.map((p, i) => ({
        ...p,
        isCurrentTurn: i === nextIndex
      }));
      return {
        ...prev,
        players: newPlayers,
        turnCount: prev.turnCount + 1
      };
    });
  };

  const updateScore = (playerId: string, delta: number) => {
    setGameState(prev => ({
      ...prev,
      players: prev.players.map(p => p.id === playerId ? { ...p, score: Math.max(0, p.score + delta) } : p)
    }));
  };

  if (screen === 'splash') {
    return <SplashScreen />;
  }

  if (screen === 'search') {
    return (
      <SearchScreen 
        onBack={() => setScreen('home')} 
        onSelectGame={(name) => {
          setSelectedGame(name);
          setScreen('detail');
        }}
      />
    );
  }

  if (screen === 'detail') {
    return (
      <GameDetailScreen 
        gameName={selectedGame}
        onBack={() => setScreen('search')}
        onStartSession={() => setScreen('moderator')}
        onSelectMode={(mode) => {
          if (mode === 'quickstart') setScreen('quickstart');
          if (mode === 'stepbystep') setScreen('stepbystep');
          if (mode === 'simulate') setScreen('simulation');
          // Other modes can be added later
        }}
      />
    );
  }

  if (screen === 'quickstart') {
    return (
      <QuickStartScreen 
        gameName={selectedGame}
        onBack={() => setScreen('detail')}
        onSkip={() => setScreen('moderator')}
        onNext={() => setScreen('moderator')}
      />
    );
  }

  if (screen === 'stepbystep') {
    return (
      <StepByStepScreen 
        gameName={selectedGame}
        onBack={() => setScreen('detail')}
        onComplete={() => setScreen('moderator')}
      />
    );
  }

  if (screen === 'simulation') {
    return (
      <SimulationScreen 
        gameName={selectedGame}
        onExit={() => setScreen('detail')}
        onStartRealGame={() => setScreen('moderator')}
      />
    );
  }

  if (screen === 'moderator') {
    return (
      <ActiveSessionScreen 
        gameName={selectedGame}
        onExit={() => setScreen('home')}
        onFinish={() => setScreen('summary')}
      />
    );
  }

  if (screen === 'summary') {
    return (
      <PostGameSummary 
        gameName={selectedGame}
        duration="1h 24m"
        winnerName="Alex"
        winnerColor="bg-player-red"
        stats={{
          disputesResolved: 7,
          houseRulesActive: 3,
          rulesExplained: 12
        }}
        onPlayAgain={() => setScreen('moderator')}
        onNewGame={() => setScreen('home')}
      />
    );
  }

  return <HomeScreen onSearch={() => setScreen('search')} onSelectGame={(name) => { setSelectedGame(name); setScreen('detail'); }} />;
}
