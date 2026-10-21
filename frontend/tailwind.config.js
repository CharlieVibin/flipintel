/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#070b09",
          900: "#0a100d",
          850: "#0d1511",
          800: "#111b16",
          700: "#17251d",
          600: "#203328",
          500: "#2b4435",
        },
        mist: {
          100: "#eef6f0",
          200: "#d6e6db",
          300: "#a9c1b2",
          400: "#7f9b8a",
          500: "#5c7567",
        },
        lime: {
          300: "#a7f3c9",
          400: "#6ee7a8",
          500: "#3ddc84",
          600: "#22b56a",
        },
        ember: "#f5b45b",
        rose: "#f27f7f",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "SFMono-Regular", "Consolas", "monospace"],
      },
      boxShadow: {
        glow: "0 0 40px -12px rgba(61, 220, 132, 0.35)",
        panel: "0 20px 50px -30px rgba(0,0,0,0.9)",
      },
      keyframes: {
        pulseSoft: {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.45", transform: "scale(0.9)" },
        },
        rise: {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        scanline: {
          "0%": { transform: "translateY(-100%)" },
          "100%": { transform: "translateY(600%)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-700px 0" },
          "100%": { backgroundPosition: "700px 0" },
        },
      },
      animation: {
        "pulse-soft": "pulseSoft 2.4s ease-in-out infinite",
        rise: "rise 0.45s ease-out both",
        scanline: "scanline 3.2s linear infinite",
        shimmer: "shimmer 1.6s linear infinite",
      },
    },
  },
  plugins: [],
};
