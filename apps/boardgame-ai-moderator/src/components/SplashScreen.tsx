import React from 'react';
import { motion } from 'motion/react';

const CubeLoader = () => {
  return (
    <div className="flex gap-4 justify-center items-center h-12">
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          className="w-4 h-4 relative isometric-container"
          initial={{ y: 0 }}
          animate={{ y: [-10, 0] }}
          transition={{
            duration: 0.6,
            repeat: Infinity,
            repeatType: "reverse",
            delay: i * 0.2,
            ease: "easeInOut"
          }}
        >
          {/* Isometric Cube */}
          <div className="absolute inset-0 bg-white/80 transform rotateX(45deg) rotateZ(45deg) shadow-sm" style={{ transformStyle: 'preserve-3d' }}>
            <div className="absolute inset-0 bg-white/60" style={{ transform: 'translateZ(-4px)' }} />
          </div>
        </motion.div>
      ))}
    </div>
  );
};

const Pawn = ({ color, position }: { color: string, position: string }) => (
  <div className={`absolute ${position} flex flex-col items-center justify-end h-16 w-8`} style={{ transform: 'translateZ(2px)' }}>
    {/* Pawn Shadow - Flat elliptical shadow directly beneath it */}
    <div className="absolute bottom-[-2px] w-8 h-4 bg-black/15 rounded-[100%] blur-[2px]" style={{ transform: 'rotateX(60deg)' }} />
    
    {/* Pawn Body - Cylinder with rounded top */}
    <div className="relative flex flex-col items-center" style={{ transformStyle: 'preserve-3d' }}>
      {/* Top Rounded Part */}
      <div className={`w-5 h-5 ${color} rounded-full shadow-inner mb-[-10px] relative z-20`} />
      {/* Cylinder Body */}
      <div className={`w-5 h-10 ${color} rounded-b-sm relative z-10 shadow-md`}>
        {/* Side shading for 3D effect */}
        <div className="absolute inset-0 bg-black/10 rounded-b-sm" style={{ clipPath: 'inset(0 0 0 50%)' }} />
      </div>
      {/* Base */}
      <div className={`w-6 h-1.5 ${color} rounded-full -mt-1 shadow-sm opacity-90`} />
    </div>
  </div>
);

export const SplashScreen = () => {
  return (
    <div className="fixed inset-0 bg-amber-brand flex flex-col items-center justify-center z-50 overflow-hidden">
      {/* Isometric Board Illustration */}
      <div className="w-[70%] max-w-[400px] aspect-square relative isometric-container flex items-center justify-center mb-16">
        <motion.div 
          className="w-full h-full relative"
          initial={{ opacity: 0, scale: 0.8, rotateX: 0, rotateZ: 0 }}
          animate={{ opacity: 1, scale: 1, rotateX: 35, rotateZ: -45 }}
          transition={{ duration: 1.2, ease: "easeOut" }}
          style={{ transformStyle: 'preserve-3d' }}
        >
          {/* Board Thickness - Right Face */}
          <div 
            className="absolute top-0 left-full w-6 h-full bg-board-beige-dark origin-left" 
            style={{ transform: 'rotateY(90deg)' }} 
          />
          
          {/* Board Thickness - Front Face */}
          <div 
            className="absolute top-full left-0 w-full h-6 bg-board-beige-mid origin-top" 
            style={{ transform: 'rotateX(-90deg)' }} 
          />

          {/* Board Top Surface */}
          <div className="absolute inset-0 bg-board-beige shadow-iso-3 border-[6px] border-board-beige-dark flex items-center justify-center">
            {/* 8x8 Grid */}
            <div className="w-full h-full grid grid-cols-8 grid-rows-8">
              {Array.from({ length: 64 }).map((_, i) => {
                const isDark = (Math.floor(i / 8) + (i % 8)) % 2 === 1;
                return (
                  <div 
                    key={i} 
                    className={`border-[0.5px] border-navy-deep/5 ${isDark ? 'bg-navy-deep/5' : 'bg-transparent'}`} 
                  />
                );
              })}
            </div>
          </div>

          {/* Pawns at Corners */}
          <Pawn color="bg-player-red" position="top-4 left-4" />
          <Pawn color="bg-player-blue" position="top-4 right-4" />
          <Pawn color="bg-player-yellow" position="bottom-4 left-4" />
          <Pawn color="bg-player-green" position="bottom-4 right-4" />
        </motion.div>
      </div>

      {/* App Branding */}
      <div className="text-center space-y-2">
        <motion.h1 
          className="text-[36px] font-display font-extrabold text-white tracking-[0.05em] leading-none"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
        >
          BOARDSAGE
        </motion.h1>
        <motion.p 
          className="text-[14px] font-sans text-white/70"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.8 }}
        >
          Your AI Game Companion
        </motion.p>
      </div>

      {/* Loading Indicator */}
      <div className="absolute bottom-16 w-full">
        <CubeLoader />
      </div>
    </div>
  );
};
