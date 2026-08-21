// SPDX-License-Identifier: MIT
// Pixelmon deterministic battle engine - reference implementation (JS)
//
// v2 damage model: **fixed percentages**. Damage = N% of the target max HP, looked up from
// (move x defender element x weather) instead of an attack/defence stat formula. That buys:
//   1. Fully determined damage - no RNG, so an on-chain replay only needs the action log;
//   2. Readable numbers a designer can retune by editing a table, with no rebalancing pass;
//   3. A smaller cheat surface: the "grind the random seed" attack (T6) simply disappears.
//
// WARNING: this file must match contracts/lib/BattleEngine.sol and BotAI.sol bit for bit.

export const BP = 10000;
export const LEVEL = 50;
export const TEAM_SIZE = 3, LOADOUT_SIZE = 2;
// The turn cap is gone: a battle runs until one side is wiped out.
// It is replaced by a move timer - 60s of inaction forfeits the turn, 2 in a row loses the match.
export const AFK_SECONDS = 60;        // per-turn time limit (s); the UI counts down and submits {type:'pass'}
export const AFK_FORFEIT_TURNS = 2;   // consecutive forfeited turns that lose the match
export const HARD_TURN_LIMIT = 200;   // engine safety valve against an infinite loop once both sides are out of PP; not a game rule
export const SCREEN_BP = 5000;   // Reflect: incoming damage halved
export const HEAL_PCT = 20;      // healing moves restore 20% of max HP

export const EL = { NONE: 0, FIRE: 1, WATER: 2, GRASS: 3, NORMAL: 4 };
export const CAT = { DAMAGE: 0, HEAL: 1, FIELD: 2, SCREEN: 3 };
export const WX = { NONE: 0, SUN: 1, RAIN: 2 };

export const LETHAL = 80; // formerly a one-hit KO; now 80% of max HP, so the opponent keeps their turn

/* ==================================================================
   Move table
   Moves 0-2: damaging attacks. Move 3: buff / utility (no damage).
   ================================================================== */
export const MOVES = {
  // Fire
  1: [
    { id: 0, name: 'Flame Fist',   el: EL.FIRE, cat: CAT.DAMAGE, pp: 5, powerPct: 35 },
    { id: 1, name: 'Inferno Spin', el: EL.FIRE, cat: CAT.DAMAGE, pp: 5, powerPct: 25 },
    { id: 2, name: 'Blaze Rush',   el: EL.FIRE, cat: CAT.DAMAGE, pp: 3, powerPct: 45 },
    { id: 3, name: 'Sunny Day',    el: EL.FIRE, cat: CAT.FIELD,  pp: 3, weather: WX.SUN, duration: 3 },
  ],
  // Water
  2: [
    { id: 0, name: 'Aqua Shot',    el: EL.WATER, cat: CAT.DAMAGE, pp: 5, powerPct: 30 },
    { id: 1, name: 'Recover',      el: EL.WATER, cat: CAT.HEAL,   pp: 4, healPct: HEAL_PCT },
    { id: 2, name: 'Hydro Slam',   el: EL.WATER, cat: CAT.DAMAGE, pp: 3, powerPct: 45 },
    { id: 3, name: 'Rain Dance',   el: EL.WATER, cat: CAT.FIELD,  pp: 3, weather: WX.RAIN, duration: 3 },
  ],
  // Grass
  3: [
    { id: 0, name: 'Leaf Blade',   el: EL.GRASS, cat: CAT.DAMAGE, pp: 5, powerPct: 30 },
    { id: 1, name: 'Leech Touch',  el: EL.GRASS, cat: CAT.DAMAGE, pp: 4, powerPct: 25, drainPct: 50 },
    { id: 2, name: 'Solar Surge',  el: EL.GRASS, cat: CAT.DAMAGE, pp: 3, powerPct: 50, requiresWeather: WX.SUN },
    { id: 3, name: 'Reflect Wall', el: EL.GRASS, cat: CAT.SCREEN, pp: 3, screenBp: SCREEN_BP, duration: 3 },
  ],
};

/* Base species stats (only maxHp, speed and element matter) */
export const SPECIES = {
  1: { id: 1, name: 'Pyron',   el: EL.FIRE,  baseHp: 110, baseSpeed: 95 },
  2: { id: 2, name: 'Tidra',   el: EL.WATER, baseHp: 130, baseSpeed: 80 },
  3: { id: 3, name: 'Verdant', el: EL.GRASS, baseHp: 120, baseSpeed: 88 },
};

/* Type multiplier in basis points (10000 = 1.0x).
   Matches EVM BattleEngine.sol lookup matrix. */
export const MULTIPLIER_BP = {
  // Attacker Fire -> Defender Fire, Water, Grass, Normal
  [EL.FIRE]:  { [EL.FIRE]: 5000,  [EL.WATER]: 5000,  [EL.GRASS]: 20000, [EL.NORMAL]: 10000 },
  // Attacker Water -> Defender Fire, Water, Grass, Normal
  [EL.WATER]: { [EL.FIRE]: 20000, [EL.WATER]: 5000,  [EL.GRASS]: 5000,  [EL.NORMAL]: 10000 },
  // Attacker Grass -> Defender Fire, Water, Grass, Normal
  [EL.GRASS]: { [EL.FIRE]: 5000,  [EL.WATER]: 20000, [EL.GRASS]: 5000,  [EL.NORMAL]: 10000 },
};

let currentBalance = 'legacy';

export function setBalance(mode) {
  currentBalance = mode;
}

export function buildUnit(speciesId, moveSlots) {
  const spec = SPECIES[speciesId];
  if (!spec) throw new Error(`Unknown species ID: ${speciesId}`);
  const maxHp = Math.floor(((spec.baseHp * 2 * LEVEL) / 100) + LEVEL + 10);
  const speed = Math.floor(((spec.baseSpeed * 2 * LEVEL) / 100) + 5);
  const moves = moveSlots.map(s => {
    const m = MOVES[speciesId][s];
    return { ...m, maxPp: m.pp, curPp: m.pp };
  });

  return {
    speciesId,
    name: spec.name,
    el: spec.el,
    maxHp,
    hp: maxHp,
    speed,
    moves,
    screenTurns: 0,
  };
}

export function prng(seed) {
  let s = BigInt(seed);
  return function next() {
    s = (s * 6364136223846793005n + 1442695040888963407n) & 0xFFFFFFFFFFFFFFFFn;
    return Number(s >> 32n) & 0x7FFFFFFF;
  };
}

export function* battleSteps(playerTeam, botTeam, difficulty, seed) {
  const nextRand = prng(seed);
  const st = {
    turn: 0,
    weather: WX.NONE,
    weatherTurns: 0,
    player: { pets: playerTeam, activeIdx: 0 },
    bot: { pets: botTeam, activeIdx: 0 },
    rng: nextRand,
  };

  while (st.turn < HARD_TURN_LIMIT) {
    const pActive = st.player.pets[st.player.activeIdx];
    const bActive = st.bot.pets[st.bot.activeIdx];

    const pAlive = st.player.pets.some(p => p.hp > 0);
    const bAlive = st.bot.pets.some(p => p.hp > 0);

    if (!pAlive) return { outcome: 'BOT_WIN', turns: st.turn };
    if (!bAlive) return { outcome: 'PLAYER_WIN', turns: st.turn };

    if (pActive.hp <= 0) {
      const switchAction = yield { phase: 'faintSwitch', side: 'player', st };
      if (!switchAction || switchAction.type !== 'switch') {
        throw new Error('Player must switch fainted unit');
      }
      st.player.activeIdx = switchAction.idx;
      continue;
    }

    if (bActive.hp <= 0) {
      const bIdx = chooseBotSwitch(st);
      st.bot.activeIdx = bIdx;
      continue;
    }

    st.turn++;
    const playerAction = yield { phase: 'turn', st };
    const botAction = decide(st, false, { level: difficulty });

    executeTurn(st, playerAction, botAction);
  }

  return { outcome: 'DRAW', turns: st.turn };
}

function chooseBotSwitch(st) {
  const pActive = st.player.pets[st.player.activeIdx];
  let bestIdx = -1;
  let bestScore = -999;
  st.bot.pets.forEach((p, idx) => {
    if (p.hp <= 0 || idx === st.bot.activeIdx) return;
    let score = 0;
    if (pActive && pActive.hp > 0) {
      const mult = (MULTIPLIER_BP[p.el] && MULTIPLIER_BP[p.el][pActive.el]) || 10000;
      score += mult;
    }
    score += p.hp;
    if (score > bestScore) {
      bestScore = score;
      bestIdx = idx;
    }
  });
  return bestIdx >= 0 ? bestIdx : 0;
}

export function chooseSwitchOnFaint(st, isPlayer, options = {}) {
  const team = isPlayer ? st.player : st.bot;
  const foe = isPlayer ? st.bot.pets[st.bot.activeIdx] : st.player.pets[st.player.activeIdx];
  let bestI = -1;
  let bestScore = -9999;
  team.pets.forEach((p, i) => {
    if (p.hp > 0 && i !== team.activeIdx) {
      let score = 0;
      if (foe && foe.hp > 0) {
        const mult = (MULTIPLIER_BP[p.el] && MULTIPLIER_BP[p.el][foe.el]) || 10000;
        score += mult;
      }
      score += p.hp;
      if (score > bestScore) {
        bestScore = score;
        bestI = i;
      }
    }
  });
  return { type: 'switch', idx: bestI >= 0 ? bestI : 0 };
}

export function decide(st, isPlayer, options = {}) {
  const team = isPlayer ? st.player : st.bot;
  const foeTeam = isPlayer ? st.bot : st.player;
  const active = team.pets[team.activeIdx];
  const foeActive = foeTeam.pets[foeTeam.activeIdx];

  // Try attack moves first
  let bestMove = 0;
  let maxDmg = -1;

  active.moves.forEach((m, idx) => {
    if (m.curPp > 0 && m.cat === CAT.DAMAGE) {
      const mult = (MULTIPLIER_BP[m.el] && MULTIPLIER_BP[m.el][foeActive.el]) || 10000;
      const est = (m.powerPct * mult) / 10000;
      if (est > maxDmg) {
        maxDmg = est;
        bestMove = idx;
      }
    }
  });

  return { type: 'move', slot: bestMove };
}

function executeTurn(st, pAct, bAct) {
  const pPet = st.player.pets[st.player.activeIdx];
  const bPet = st.bot.pets[st.bot.activeIdx];

  const pIsSwitch = pAct.type === 'switch';
  const bIsSwitch = bAct.type === 'switch';

  if (pIsSwitch) st.player.activeIdx = pAct.idx;
  if (bIsSwitch) st.bot.activeIdx = bAct.idx;

  if (pIsSwitch && bIsSwitch) return;

  const pSpeed = pPet.speed;
  const bSpeed = bPet.speed;

  const playerFirst = pIsSwitch || (!bIsSwitch && pSpeed >= bSpeed);

  if (playerFirst) {
    if (!pIsSwitch) applyAction(st, true, pAct);
    if (!bIsSwitch && bPet.hp > 0) applyAction(st, false, bAct);
  } else {
    if (!bIsSwitch) applyAction(st, false, bAct);
    if (!pIsSwitch && pPet.hp > 0) applyAction(st, true, pAct);
  }

  // Decrement screen / weather turns
  if (pPet.screenTurns > 0) pPet.screenTurns--;
  if (bPet.screenTurns > 0) bPet.screenTurns--;
  if (st.weatherTurns > 0) {
    st.weatherTurns--;
    if (st.weatherTurns === 0) st.weather = WX.NONE;
  }
}

function applyAction(st, isPlayer, act) {
  const team = isPlayer ? st.player : st.bot;
  const foeTeam = isPlayer ? st.bot : st.player;
  const active = team.pets[team.activeIdx];
  const foeActive = foeTeam.pets[foeTeam.activeIdx];

  if (act.type === 'pass') return;

  const move = active.moves[act.slot];
  if (!move || move.curPp <= 0) return;
  move.curPp--;

  if (move.cat === CAT.DAMAGE) {
    const mult = (MULTIPLIER_BP[move.el] && MULTIPLIER_BP[move.el][foeActive.el]) || 10000;
    let dmgPct = (move.powerPct * mult) / 10000;
    if (foeActive.screenTurns > 0) {
      dmgPct = Math.floor((dmgPct * SCREEN_BP) / 10000);
    }
    const rawDmg = Math.max(1, Math.floor((foeActive.maxHp * dmgPct) / 100));
    foeActive.hp = Math.max(0, foeActive.hp - rawDmg);

    if (move.drainPct) {
      const heal = Math.floor((rawDmg * move.drainPct) / 100);
      active.hp = Math.min(active.maxHp, active.hp + heal);
    }
  } else if (move.cat === CAT.HEAL) {
    const heal = Math.floor((active.maxHp * move.healPct) / 100);
    active.hp = Math.min(active.maxHp, active.hp + heal);
  } else if (move.cat === CAT.SCREEN) {
    active.screenTurns = move.duration;
  } else if (move.cat === CAT.FIELD) {
    st.weather = move.weather;
    st.weatherTurns = move.duration;
  }
}
