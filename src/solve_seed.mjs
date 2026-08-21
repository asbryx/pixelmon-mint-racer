import { buildUnit, battleSteps, setBalance, chooseSwitchOnFaint } from './pixelmon_engine.mjs';

setBalance('tuned');

// Optimized team setup:
// 1. Verdant (Grass) with Leaf Blade [0], Reflect Wall [3]
// 2. Tidra (Water) with Aqua Shot [0], Recover [1]
// 3. Pyron (Fire) with Flame Fist [0], Blaze Rush [2]
export const DEFAULT_PLAYER_TEAM = [
  { speciesId: 3, slots: [0, 3] },
  { speciesId: 2, slots: [0, 1] },
  { speciesId: 1, slots: [0, 2] },
];

export const DEFAULT_BOT_TEAM = [
  { speciesId: 2, slots: [0, 2] },
  { speciesId: 1, slots: [0, 1] },
  { speciesId: 3, slots: [3, 0] },
];

export function playOptimalBattle(seed, difficulty = 3) {
  const playerPets = DEFAULT_PLAYER_TEAM.map((p) => buildUnit(p.speciesId, p.slots));
  const botPets = DEFAULT_BOT_TEAM.map((p) => buildUnit(p.speciesId, p.slots));
  const it = battleSteps(playerPets, botPets, difficulty, seed);
  const actions = [];
  let cur = it.next();

  while (!cur.done) {
    const { phase, st } = cur.value;
    if (phase === 'faintSwitch') {
      const act = chooseSwitchOnFaint(st, true, { level: difficulty });
      actions.push(act);
      cur = it.next(act);
    } else {
      // Prioritize high-damage moves with PP
      const active = st.player.pets[st.player.activeIdx];
      let chosenSlot = 0;
      for (let i = 0; i < active.moves.length; i++) {
        if (active.moves[i].curPp > 0) {
          chosenSlot = i;
          break;
        }
      }
      const act = { type: 'move', slot: chosenSlot };
      actions.push(act);
      cur = it.next(act);
    }
  }

  return {
    outcome: cur.value.outcome,
    turns: cur.value.turns,
    actions,
  };
}

if (process.argv[1] && process.argv[1].endsWith('solve_seed.mjs')) {
  const seedArg = process.argv[2] ? BigInt(process.argv[2]) : 0n;
  const result = playOptimalBattle(seedArg);
  console.log(JSON.stringify(result));
}
