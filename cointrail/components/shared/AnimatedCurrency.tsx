"use client";

import { useRef, useEffect } from "react";
import { useInView, useSpring, useMotionValue } from "motion/react";
import { autoFormatCurrency } from "@/lib/formatters";

/**
 * Spring-physics currency counter that rolls from 0 to target value on scroll into view.
 * Direct DOM mutation for performance (no React re-renders per frame).
 */
export function AnimatedCurrency({
  value,
  delay = 0,
  className,
}: {
  value: number;
  delay?: number;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const isInView = useInView(ref, { once: true, margin: "-50px" });
  const motionVal = useMotionValue(0);
  const spring = useSpring(motionVal, { stiffness: 50, damping: 20, mass: 1 });

  useEffect(() => {
    if (isInView) {
      const t = setTimeout(() => motionVal.set(value), delay);
      return () => clearTimeout(t);
    }
  }, [isInView, value, delay, motionVal]);

  useEffect(() => {
    const unsub = spring.on("change", (v) => {
      if (ref.current) ref.current.textContent = autoFormatCurrency(Math.round(v));
    });
    return unsub;
  }, [spring]);

  return (
    <span ref={ref} className={className} style={{ fontVariantNumeric: "tabular-nums" }}>
      ₹0
    </span>
  );
}

/**
 * Spring-physics number counter (non-currency).
 */
export function AnimatedNumber({
  value,
  delay = 0,
  className,
}: {
  value: number;
  delay?: number;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const isInView = useInView(ref, { once: true, margin: "-50px" });
  const motionVal = useMotionValue(0);
  const spring = useSpring(motionVal, { stiffness: 60, damping: 20 });

  useEffect(() => {
    if (isInView) {
      const t = setTimeout(() => motionVal.set(value), delay);
      return () => clearTimeout(t);
    }
  }, [isInView, value, delay, motionVal]);

  useEffect(() => {
    const unsub = spring.on("change", (v) => {
      if (ref.current) ref.current.textContent = String(Math.round(v));
    });
    return unsub;
  }, [spring]);

  return (
    <span ref={ref} className={className} style={{ fontVariantNumeric: "tabular-nums" }}>
      0
    </span>
  );
}
