"use client";

import { useRef, useState, useCallback } from "react";
import { motion } from "motion/react";
import { AnimatedCurrency, AnimatedNumber } from "./AnimatedCurrency";

/* ═══════════════════════════════════════════════════════════════════════════
   GradientStatCard — dark gradient bg, 3D mouse-tilt, hover sub-text reveal
   Used on: Credit Cards page + Expenses dashboard
   ═══════════════════════════════════════════════════════════════════════════ */

const STAT_GRADIENTS: Record<string, { bg: string }> = {
  red: { bg: "from-[#2a0a0a] via-[#3d1010] to-[#1a0505]" },
  green: { bg: "from-[#0a2a15] via-[#103d22] to-[#051a0d]" },
  blue: { bg: "from-[#0a152a] via-[#10223d] to-[#050d1a]" },
  purple: { bg: "from-[#1a0a2a] via-[#28103d] to-[#051a14]" },
  shield: { bg: "from-[#0a2a20] via-[#103d2e] to-[#051a14]" },
  amber: { bg: "from-[#2a1f0a] via-[#3d2c10] to-[#1a1405]" },
  cyan: { bg: "from-[#0a2027] via-[#0e3039] to-[#05141a]" },
};

export function GradientStatCard({
  title,
  value,
  sub,
  icon: Icon,
  gradientKey,
  delay,
  isCurrency = true,
  displayText,
}: {
  title: string;
  value: number;
  sub?: string;
  icon: React.ElementType;
  gradientKey: string;
  delay: number;
  isCurrency?: boolean;
  displayText?: string;
}) {
  const cardRef = useRef<HTMLDivElement>(null);
  const [tilt, setTilt] = useState({ x: 0, y: 0 });
  const [hovered, setHovered] = useState(false);
  const theme = STAT_GRADIENTS[gradientKey] || STAT_GRADIENTS.blue;

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!cardRef.current) return;
    const rect = cardRef.current.getBoundingClientRect();
    const px = (e.clientX - rect.left) / rect.width;
    const py = (e.clientY - rect.top) / rect.height;
    setTilt({ x: (py - 0.5) * -15, y: (px - 0.5) * 15 });
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: delay * 0.1, ease: [0.25, 1, 0.5, 1] }}
      className="relative cursor-default"
      style={{ perspective: "800px" }}
    >
      <div
        ref={cardRef}
        className={`relative rounded-2xl bg-gradient-to-br p-5 ${theme.bg} overflow-hidden text-white`}
        onMouseMove={handleMouseMove}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => {
          setTilt({ x: 0, y: 0 });
          setHovered(false);
        }}
        style={{
          transition: hovered
            ? "box-shadow 0.2s ease"
            : "all 0.5s cubic-bezier(0.25, 0.46, 0.45, 0.94)",
          transform: `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg) ${hovered ? "translateY(-4px)" : ""}`,
          transformStyle: "preserve-3d",
          boxShadow: hovered ? "0 20px 50px rgba(0,0,0,0.5)" : "0 4px 16px rgba(0,0,0,0.3)",
        }}
      >
        <div
          className="absolute inset-0 opacity-[0.04]"
          style={{
            backgroundSize: "18px 18px",
            backgroundImage:
              "linear-gradient(rgba(255,255,255,0.3) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.3) 1px, transparent 1px)",
          }}
        />
        <div className="absolute top-0 right-0 left-0 h-[1px] bg-gradient-to-r from-transparent via-white/15 to-transparent" />

        <div className="relative z-10 flex h-full flex-col">
          <div className="mb-4 flex items-center gap-2.5">
            <div
              className="rounded-xl bg-white/[0.06] p-2"
              style={{ boxShadow: "0 0 0 1px rgba(255,255,255,0.06)" }}
            >
              <Icon className="h-4 w-4 text-white/50" />
            </div>
            <span className="text-sm font-semibold tracking-wide text-white/70">{title}</span>
          </div>
          {displayText ? (
            <p
              className="text-2xl font-bold text-white"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {displayText}
            </p>
          ) : (
            <p
              className="text-2xl font-bold text-white"
              style={{ fontVariantNumeric: "tabular-nums" }}
            >
              {isCurrency ? (
                <AnimatedCurrency value={value} delay={delay * 100} />
              ) : (
                <AnimatedNumber value={value} delay={delay * 100} />
              )}
            </p>
          )}
          <div
            style={{
              overflow: "hidden",
              maxHeight: hovered ? "24px" : "0",
              opacity: hovered ? 1 : 0,
              transform: hovered ? "translateY(0)" : "translateY(6px)",
              transition: "all 0.4s cubic-bezier(0.25, 0.46, 0.45, 0.94)",
              marginTop: hovered ? "6px" : "0",
            }}
          >
            <p className="text-xs text-white/35">{sub}</p>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   GlassStatCard — glassmorphism card with accent glow orb, 3D tilt
   Used on: Expenses dashboard
   ═══════════════════════════════════════════════════════════════════════════ */

export function GlassStatCard({
  icon: Icon,
  label,
  children,
  accent,
  delay = 0,
}: {
  icon: React.ElementType;
  label: string;
  children: React.ReactNode;
  accent: string;
  delay?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [tilt, setTilt] = useState({ x: 0, y: 0 });

  const handleMouse = useCallback((e: React.MouseEvent) => {
    if (!ref.current) return;
    const rect = ref.current.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width - 0.5) * 8;
    const y = ((e.clientY - rect.top) / rect.height - 0.5) * -8;
    setTilt({ x, y });
  }, []);

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay }}
      onMouseMove={handleMouse}
      onMouseLeave={() => setTilt({ x: 0, y: 0 })}
      className="glass-card group relative cursor-default overflow-hidden p-4"
      style={{
        transform: `perspective(800px) rotateX(${tilt.y}deg) rotateY(${tilt.x}deg)`,
        transition: "transform 0.15s ease-out",
      }}
    >
      <div
        className="absolute -top-6 -right-6 h-20 w-20 rounded-full opacity-20 blur-2xl transition-opacity group-hover:opacity-30"
        style={{ backgroundColor: accent }}
      />
      <div className="mb-2 flex items-center gap-2">
        <div className="rounded-lg p-1.5" style={{ backgroundColor: accent + "18" }}>
          <Icon className="h-4 w-4" style={{ color: accent }} />
        </div>
        <span className="text-xs font-medium tracking-wider text-[var(--text-muted)] uppercase">
          {label}
        </span>
      </div>
      <div className="relative z-10">{children}</div>
    </motion.div>
  );
}
