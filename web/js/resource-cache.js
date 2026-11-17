/* Shared requests stay cached until their underlying library data changes. */
export function createResourceCache(load) {
  let generation = 0;
  let cached = null;
  return {
    read() {
      if (!cached) {
        const current = generation;
        cached = Promise.resolve().then(load).catch(error => {
          if (generation === current) cached = null;
          throw error;
        });
      }
      return cached;
    },
    invalidate() {
      generation += 1;
      cached = null;
    },
  };
}
