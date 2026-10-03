/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: "#0B1120",
          panel: "#121A2E",
          raised: "#182238",
          line: "#243252",
        },
        signal: {
          amber: "#F2A93B",
          red: "#E5484D",
          teal: "#2BC8A8",
          blue: "#5B8DEF",
        },
        mist: {
          DEFAULT: "#E7ECF5",
          dim: "#8C97B3",
          faint: "#576081",
        },
      },
      fontFamily: {
        display: ["Rajdhani", "sans-serif"],
        body: ["Inter", "sans-serif"],
        mono: ["IBM Plex Mono", "monospace"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(242,169,59,0.15), 0 0 24px rgba(242,169,59,0.08)",
      },
      keyframes: {
        sweep: {
          "0%": { transform: "rotate(-120deg)" },
          "100%": { transform: "rotate(var(--needle-angle))" },
        },
        pulseDot: {
          "0%, 100%": { opacity: 1 },
          "50%": { opacity: 0.35 },
        },
      },
      animation: {
        sweep: "sweep 0.9s cubic-bezier(0.22,1,0.36,1) forwards",
        pulseDot: "pulseDot 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
