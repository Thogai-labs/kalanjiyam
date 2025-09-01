module.exports = {
  content: [
    "./kalanjiyam/static/js/*.js",
    "./kalanjiyam/templates/**/*.html",
    "./kalanjiyam/utils/parse_alignment.py",
    "./kalanjiyam/utils/xml.py",
    "./kalanjiyam/views/proofing/main.py",
    "./kalanjiyam/static/js/proofer.js",
  ],
  safelist: [
    // Used by Flask-admin internally -- include it explicitly
    "pagination",
    // Custom height classes used in proofing interface
    "h-[90vh]",
    // Arbitrary value classes that might be used
    "h-[90vh]",
    "grid-cols-6",
    "grid-cols-8",
    "grid-cols-10",
    "w-48",
    "w-60",
    "w-80",
  ],
  theme: {
    extend: {
      colors: {
        "peacock-primary": "#0f766e", // Teal-800
        "peacock-secondary": "#0891b2", // Cyan-600
        "peacock-accent": "#7c3aed", // Violet-600
        "peacock-emerald": "#059669", // Emerald-600
        "peacock-gold": "#fbbf24", // Amber-400
        "peacock-subtle": "#f0fdfa", // Teal-50
        "peacock-light": "#ccfbf1", // Teal-100
        "peacock-medium": "#99f6e4", // Teal-200
      },
    },
  },
  plugins: [
    require("@tailwindcss/typography")({
      className: "tw-prose",
    }),
  ],
};
