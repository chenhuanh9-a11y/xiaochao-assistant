(function(root, factory) {
  "use strict";
  function createForFloors(ids) {
    var selected = ids == null ? ["minus-1", "1", "2"] : ids;
    if (!Array.isArray(selected) || selected.some(function (id) { return typeof id !== "string"; }) || ["minus-1,1,2", "b1,b2,b3", "1,2"].indexOf(selected.join(",")) < 0) throw Error("路线楼层范围无效");
    var result = factory(selected.slice());
    result.createForFloors = createForFloors;
    return result;
  }
  var api = createForFloors();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.NightmareRouteModel = api;
})(typeof self !== "undefined" ? self : this, function(floorIds) {
  "use strict";
  var FLOORS = Object.freeze(floorIds.slice());
  var MAX_POINTS = FLOORS.join(",") === "1,2" ? 512 : 128;
  function decode(runs, length, Type) {
    var result = new Type(length), offset = 0;
    if (!Array.isArray(runs) || runs.length % 2) throw Error("导航数据格式错误");
    for (var i = 0; i < runs.length; i += 2) {
      var value = runs[i], count = runs[i + 1];
      if (!Number.isInteger(value) || !Number.isInteger(count) || count < 1 || offset + count > length) throw Error("导航数据长度错误");
      result.fill(value, offset, offset + count); offset += count;
    }
    if (offset !== length) throw Error("导航数据不完整");
    return result;
  }
  function prepare(raw) {
    var size = raw.cols * raw.rows;
    if (!Number.isInteger(size) || size < 1 || size > 1000000 || raw.step < 1 || raw.width < 1 || raw.height < 1) throw Error("地图导航尺寸错误");
    var nav = {
      cols: raw.cols, rows: raw.rows, step: raw.step, width: raw.width, height: raw.height,
      edges: decode(raw.edges, size, Uint8Array), labels: decode(raw.labels, size, Uint16Array),
      rooms: raw.rooms || [], size: size, unitsPerPixel: raw.unitsPerPixel || 1
    };
    nav.clearance = clearance(nav);
    // Soft wall cost: narrow doors stay traversable, while a longer centre line
    // can beat the geometrically shortest path around the inside of a bend.
    if (raw.source && (raw.source.kind === "game-road" || raw.source.kind === "source-room-connectivity")) nav.walkingCosts = walkingCosts(nav);
    nav.outlineStrips = outlineStrips(nav);
    nav.selectionLabels = selectionLabels(nav);
    return nav;
  }
  function selectionLabels(nav) {
    var labels = nav.labels.slice();
    nav.rooms.forEach(function(room) {
      if (!room.bounds || room.selectionOnly || room.area <= 0) return;
      var left = room.bounds[0], top = room.bounds[1], width = room.bounds[2], height = room.bounds[3];
      var seen = new Uint8Array(width * height), queue = new Int32Array(width * height), head = 0, tail = 0;
      function visit(x, y) {
        var at = y * width + x, cell = (top + y) * nav.cols + left + x;
        if (!seen[at] && !nav.labels[cell]) { seen[at] = 1; queue[tail++] = at; }
      }
      for (var x = 0; x < width; x++) { visit(x, 0); visit(x, height - 1); }
      for (var y = 0; y < height; y++) { visit(0, y); visit(width - 1, y); }
      while (head < tail) {
        var at = queue[head++], xx = at % width, yy = Math.floor(at / width);
        if (xx) visit(xx - 1, yy); if (xx + 1 < width) visit(xx + 1, yy);
        if (yy) visit(xx, yy - 1); if (yy + 1 < height) visit(xx, yy + 1);
      }
      for (var i = 0; i < seen.length; i++) {
        var cell = (top + Math.floor(i / width)) * nav.cols + left + i % width;
        if (!seen[i] && !labels[cell]) labels[cell] = room.id;
      }
    });
    return labels;
  }
  // Long one-cell-wide drawing remnants are not corridors. Short door gaps remain usable.
  function outlineStrips(nav) {
    var blocked = new Uint8Array(nav.size);
    function axis(cell) { return !nav.labels[cell] && nav.clearance[cell] <= 3 ? nav.edges[cell] & 15 : 0; }
    for (var cell = 0; cell < nav.size; cell++) {
      var mask = axis(cell), delta = mask === 5 ? 1 : mask === 10 ? nav.cols : 0;
      if (!delta) continue;
      var length = 1;
      for (var sign = -1; sign <= 1; sign += 2) {
        var at = cell;
        while (length < 8 && (nav.edges[at] & (delta === 1 ? (sign < 0 ? 4 : 1) : (sign < 0 ? 8 : 2)))) {
          at += sign * delta;
          if (axis(at) !== mask) break;
          length++;
        }
      }
      if (length >= 8) blocked[cell] = 1;
    }
    return blocked;
  }
  function clearance(nav) {
    var distance = new Uint16Array(nav.size), cols = nav.cols;
    for (var i = 0; i < nav.size; i++) distance[i] = !nav.edges[i] ? 0 : (nav.edges[i] & 15) !== 15 ? 3 : 30000;
    for (var at = 0; at < nav.size; at++) {
      var x = at % cols;
      if (x) distance[at] = Math.min(distance[at], distance[at - 1] + 3);
      if (at >= cols) {
        distance[at] = Math.min(distance[at], distance[at - cols] + 3);
        if (x) distance[at] = Math.min(distance[at], distance[at - cols - 1] + 4);
        if (x < cols - 1) distance[at] = Math.min(distance[at], distance[at - cols + 1] + 4);
      }
    }
    for (var last = nav.size - 1; last >= 0; last--) {
      var column = last % cols;
      if (column < cols - 1) distance[last] = Math.min(distance[last], distance[last + 1] + 3);
      if (last + cols < nav.size) {
        distance[last] = Math.min(distance[last], distance[last + cols] + 3);
        if (column) distance[last] = Math.min(distance[last], distance[last + cols - 1] + 4);
        if (column < cols - 1) distance[last] = Math.min(distance[last], distance[last + cols + 1] + 4);
      }
    }
    return distance;
  }
  function walkingCosts(nav) {
    var costs = new Float64Array(nav.size);
    for (var at = 0; at < nav.size; at++) {
      var gap = (nav.clearance[at] / 3 + .25) * nav.step;
      costs[at] = Math.min(4, 36 / (gap * gap));
    }
    return costs;
  }
  // Supercover traversal checks every crossed edge, including both sides of a corner.
  function lineClear(nav, start, end) {
    if (!nav.edges[start] || !nav.edges[end]) return false;
    var x = start % nav.cols, y = Math.floor(start / nav.cols), tx = end % nav.cols, ty = Math.floor(end / nav.cols);
    var dx = tx - x, dy = ty - y, sx = Math.sign(dx), sy = Math.sign(dy);
    var stepX = dx ? 1 / Math.abs(dx) : Infinity, stepY = dy ? 1 / Math.abs(dy) : Infinity;
    var maxX = stepX / 2, maxY = stepY / 2, horizontal = sx > 0 ? 1 : 4, vertical = sy > 0 ? 2 : 8;
    var cell = start;
    while (x !== tx || y !== ty) {
      if (Math.abs(maxX - maxY) < 1e-10) {
        var side = cell + sx, below = cell + sy * nav.cols;
        if (!(nav.edges[cell] & horizontal) || !(nav.edges[cell] & vertical) ||
            !(nav.edges[side] & vertical) || !(nav.edges[below] & horizontal)) return false;
        x += sx; y += sy; maxX += stepX; maxY += stepY;
      } else if (maxX < maxY) {
        if (!(nav.edges[cell] & horizontal)) return false;
        x += sx; maxX += stepX;
      } else {
        if (!(nav.edges[cell] & vertical)) return false;
        y += sy; maxY += stepY;
      }
      cell = y * nav.cols + x;
      if (cell < 0 || cell >= nav.size || !nav.edges[cell]) return false;
    }
    return true;
  }
  function straightPath(nav, cells) {
    if (cells.length < 2) return cells.slice();
    var expanded = [cells[0]];
    for (var i = 1; i < cells.length; i++) {
      var a = expanded[expanded.length - 1], b = cells[i];
      if (a % nav.cols !== b % nav.cols && Math.floor(a / nav.cols) !== Math.floor(b / nav.cols)) {
        var corner = Math.floor(a / nav.cols) * nav.cols + b % nav.cols;
        if (!lineClear(nav, a, corner) || !lineClear(nav, corner, b)) corner = Math.floor(b / nav.cols) * nav.cols + a % nav.cols;
        if (!lineClear(nav, a, corner) || !lineClear(nav, corner, b)) throw Error("路线转角不在可走道路内");
        expanded.push(corner);
      }
      if (b !== expanded[expanded.length - 1]) expanded.push(b);
    }
    var costs = [0];
    for (var c = 1; c < expanded.length; c++) costs[c] = costs[c - 1] + axisCost(nav, expanded[c - 1], expanded[c]);
    var result = [expanded[0]], anchor = 0;
    while (anchor < expanded.length - 1) {
      var next = anchor + 1, bend = -1;
      for (var end = expanded.length - 1; end > anchor; end--) {
        var start = expanded[anchor], finish = expanded[end];
        if (diagonalCorridor(nav, expanded, anchor, end)) { next = end; break; }
        var options = [Math.floor(start / nav.cols) * nav.cols + finish % nav.cols,
          Math.floor(finish / nav.cols) * nav.cols + start % nav.cols];
        for (var option = 0; option < 2; option++) {
          if (lineClear(nav, start, options[option]) && lineClear(nav, options[option], finish) &&
              axisCost(nav, start, options[option]) + axisCost(nav, options[option], finish) <= (costs[end] - costs[anchor]) * 1.03 + 1e-8) { next = end; bend = options[option]; break; }
        }
        if (bend >= 0) break;
      }
      if (bend >= 0 && bend !== result[result.length - 1] && bend !== expanded[next]) result.push(bend);
      if (expanded[next] !== result[result.length - 1]) result.push(expanded[next]);
      anchor = next;
    }
    result = result.filter(function(cell, index) {
      if (!index || index === result.length - 1) return true;
      var a = result[index - 1], b = result[index + 1];
      return !((a % nav.cols === cell % nav.cols && cell % nav.cols === b % nav.cols) ||
        (Math.floor(a / nav.cols) === Math.floor(cell / nav.cols) && Math.floor(cell / nav.cols) === Math.floor(b / nav.cols)));
    });
    // Only an actual diagonal corridor may remain diagonal. Room paths use right angles.
    var output = [result[0]], at = 0;
    while (at < result.length - 1) {
      var target = at + 1;
      for (var last = result.length - 1; last >= at + 2; last--) {
        if (diagonalCorridor(nav, result, at, last)) { target = last; break; }
      }
      output.push(result[target]); at = target;
    }
    // A second pass measures each run over its final extent, after the diagonals have moved its corners.
    return centerPath(nav, centerPath(nav, output));
  }
  // Do not let right-angle simplification undo the planner's wall avoidance.
  function axisCost(nav, a, b) {
    var length = Math.max(Math.abs(a % nav.cols - b % nav.cols), Math.abs(Math.floor(a / nav.cols) - Math.floor(b / nav.cols)));
    if (!length) return 0;
    if (!nav.walkingCosts) return length;
    var stride = (b - a) / length, cost = (nav.walkingCosts[a] + nav.walkingCosts[b]) / 2;
    for (var i = 1; i < length; i++) cost += nav.walkingCosts[a + stride * i];
    return length + cost;
  }
  // Cheat sheets draw runs on the corridor centre line. Game-road labels cover corridors too,
  // so a corridor is told apart by its wall-to-wall width (shipped corridors are 36-52 px).
  var CORRIDOR_PX = 56;
  function corridorAxis(nav, a, b, horizontal) {
    var original = horizontal ? Math.floor(a / nav.cols) : a % nav.cols;
    var length = horizontal ? Math.abs(b % nav.cols - a % nav.cols) : Math.abs(Math.floor(b / nav.cols) - Math.floor(a / nav.cols));
    var stride = Math.sign(b - a) * (horizontal ? 1 : nav.cols), intervals = [], limit = Math.floor(CORRIDOR_PX / nav.step);
    for (var i = 1; i < length; i++) {
      var at = a + stride * i;
      var rays = horizontal ? [[8, -nav.cols], [2, nav.cols]] : [[4, -1], [1, 1]], spans = [];
      for (var ray = 0; ray < 2; ray++) {
        var cursor = at, count = 0;
        while (count < 48 && (nav.edges[cursor] & rays[ray][0])) { cursor += rays[ray][1]; count++; }
        spans.push(count);
      }
      var width = spans[0] + spans[1] + 1;
      if (width >= 5 && width <= limit) intervals.push([original - spans[0], original + spans[1]]);
    }
    // Runs mostly through rooms keep their position; junctions and stubs only widen single sections.
    if (intervals.length < 3 || intervals.length * 5 < length) return clearestAxis(nav, a, b, horizontal, original, length);
    var low = -Infinity, high = Infinity, best = original, bestScore = Infinity;
    intervals.forEach(function(p) { low = Math.max(low, p[0]); high = Math.min(high, p[1]); });
    for (var value = low; value <= high; value++) {
      // The adjacent runs will move the corners too. Checking the old endpoints here
      // pins a whole corridor to a doorway or an off-centre required point.
      var score = 0;
      intervals.forEach(function(p) { score += 1 / Math.pow(Math.min(value - p[0], p[1] - value) + .5, 2); });
      if (score < bestScore) { best = value; bestScore = score; }
    }
    return best;
  }
  // Runs outside corridors (junction boxes, doorways, rooms) move to the parallel line whose closest wall is
  // farthest away; ties keep the run nearest its pathfinding position.
  function clearestAxis(nav, a, b, horizontal, original, length) {
    if (length < 2) return original;
    var distance = nav.clearance || (nav.clearance = clearance(nav));
    // A run never slides further than half a corridor width, so runs across a corridor stay where they cross it.
    var stride = Math.sign(b - a) * (horizontal ? 1 : nav.cols), shift = horizontal ? nav.cols : 1, best = original, bestScore = -1, reach = Math.floor(CORRIDOR_PX / nav.step / 2);
    for (var side = -1; side <= 1; side += 2) {
      for (var offset = side < 0 ? 0 : 1; offset <= reach; offset++) {
        var from = a + side * offset * shift, to = b + side * offset * shift, line = (horizontal ? Math.floor(a / nav.cols) : a % nav.cols) + side * offset;
        if (line < 0 || line >= (horizontal ? nav.rows : nav.cols) || !lineClear(nav, from, to)) break;
        // Corners at the run's ends are shared with the next run, so a few end sections are not scored.
        var score = Infinity, margin = Math.min(3, Math.floor(length / 4));
        for (var i = 1 + margin; i < length - margin; i++) score = Math.min(score, distance[from + stride * i]);
        if (score > bestScore || (score === bestScore && offset < Math.abs(best - original))) { best = original + side * offset; bestScore = score; }
      }
    }
    return best;
  }
  function centerPath(nav, cells) {
    if (cells.length < 2) return cells;
    var kinds = [], original = [], centers = [];
    for (var i = 1; i < cells.length; i++) {
      var a = cells[i - 1], b = cells[i], kind = Math.floor(a / nav.cols) === Math.floor(b / nav.cols) ? 1 : a % nav.cols === b % nav.cols ? 2 : 0;
      kinds.push(kind); original.push(kind === 1 ? Math.floor(a / nav.cols) : kind === 2 ? a % nav.cols : 0);
      centers.push(kind ? corridorAxis(nav, a, b, kind === 1) : diagonalOffset(nav, a, b));
    }
    // Keep the requested endpoints, not the axes of their entire first/last runs.
    // A short perpendicular connector joins an off-centre endpoint to the centre line.
    if (!kinds[0]) centers[0] = 0;
    if (!kinds[kinds.length - 1]) centers[centers.length - 1] = 0;
    [0, kinds.length - 1].forEach(function(leg) {
      var a = cells[leg], b = cells[leg + 1], length = Math.max(Math.abs(a % nav.cols - b % nav.cols), Math.abs(Math.floor(a / nav.cols) - Math.floor(b / nav.cols)));
      if (kinds[leg] && length < Math.abs(centers[leg] - original[leg]) * 2 + 6) centers[leg] = original[leg];
    });
    // Two diagonals in a row keep their shared corner.
    kinds.forEach(function(kind, i) { if (!kind && (!kinds[i - 1] && i || kinds[i + 1] === 0)) centers[i] = 0; });
    function line(leg) {
      var a = cells[leg], b = cells[leg + 1], ax = a % nav.cols, ay = Math.floor(a / nav.cols), dx = b % nav.cols - ax, dy = Math.floor(b / nav.cols) - ay, length = Math.sqrt(dx * dx + dy * dy);
      return { x: ax - dy / length * centers[leg], y: ay + dx / length * centers[leg], dx: dx, dy: dy };
    }
    function corner(index) {
      var cell = cells[index], x = cell % nav.cols, y = Math.floor(cell / nav.cols), before = kinds[index - 1], after = kinds[index];
      if (before && after) {
        if (before === 1) y = centers[index - 1]; else x = centers[index - 1];
        if (after === 1) y = centers[index]; else x = centers[index];
      } else if (before || after) {
        var straight = before ? index - 1 : index, slanted = line(before ? index : index - 1);
        if (kinds[straight] === 1) { y = centers[straight]; x = Math.round(slanted.x + (y - slanted.y) * slanted.dx / slanted.dy); }
        else { x = centers[straight]; y = Math.round(slanted.y + (x - slanted.x) * slanted.dy / slanted.dx); }
      }
      return y * nav.cols + x;
    }
    // A run that cannot move first goes back alone; only a run that still fails takes its neighbours back too.
    var strikes = kinds.map(function() { return 0; });
    for (var attempt = 0; attempt <= kinds.length * 2; attempt++) {
      var result = cells.map(function(cell, index) {
        if (index && index < cells.length - 1) return corner(index);
        var leg = index ? kinds.length - 1 : 0;
        return kinds[leg] === 1 ? centers[leg] * nav.cols + cell % nav.cols
          : kinds[leg] === 2 ? Math.floor(cell / nav.cols) * nav.cols + centers[leg] : cell;
      }), invalid = [];
      if (!lineClear(nav, cells[0], result[0])) invalid.push(0);
      if (!lineClear(nav, result[result.length - 1], cells[cells.length - 1])) invalid.push(kinds.length - 1);
      for (var leg = 0; leg < kinds.length; leg++) if (!lineClear(nav, result[leg], result[leg + 1]) ||
          (kinds[leg] === 1 && Math.floor(result[leg] / nav.cols) !== Math.floor(result[leg + 1] / nav.cols)) ||
          (kinds[leg] === 2 && result[leg] % nav.cols !== result[leg + 1] % nav.cols) ||
          (!kinds[leg] && (result[leg] % nav.cols - result[leg + 1] % nav.cols) * (cells[leg] % nav.cols - cells[leg + 1] % nav.cols) <= 0)) invalid.push(leg);
      if (!invalid.length) {
        if (result[0] !== cells[0]) result.unshift(cells[0]);
        if (result[result.length - 1] !== cells[cells.length - 1]) result.push(cells[cells.length - 1]);
        result = result.filter(function(cell, i) { return !i || cell !== result[i - 1]; });
        return result.filter(function(cell, i) {
          if (!i || i === result.length - 1) return true;
          var a = result[i - 1], b = result[i + 1];
          return !((a % nav.cols === cell % nav.cols && cell % nav.cols === b % nav.cols) ||
            (Math.floor(a / nav.cols) === Math.floor(cell / nav.cols) && Math.floor(cell / nav.cols) === Math.floor(b / nav.cols)));
        });
      }
      invalid.forEach(function(leg) {
        if (!strikes[leg]++ && centers[leg] !== original[leg]) { centers[leg] = original[leg]; return; }
        for (var at = Math.max(0, leg - 1); at <= Math.min(kinds.length - 1, leg + 1); at++) centers[at] = original[at];
      });
    }
    return cells;
  }
  // Shift of a diagonal run onto its corridor centre line, in cells along the run's left normal.
  function diagonalOffset(nav, a, b) {
    var x0 = a % nav.cols, y0 = Math.floor(a / nav.cols), dx = b % nav.cols - x0, dy = Math.floor(b / nav.cols) - y0;
    var samples = Math.max(Math.abs(dx), Math.abs(dy)), shifts = [];
    for (var step = 1; step < samples; step++) {
      var spans = diagonalSpans(nav, x0 + dx * step / samples, y0 + dy * step / samples, dx, dy);
      if (spans[0] + spans[1] <= CORRIDOR_PX / nav.step) shifts.push((spans[1] - spans[0]) / 2);
    }
    if (shifts.length < 3) return 0;
    shifts.sort(function(p, q) { return p - q; });
    return shifts[Math.floor(shifts.length / 2)];
  }
  function diagonalCorridor(nav, cells, from, to) {
    var a = cells[from], b = cells[to], x0 = a % nav.cols, y0 = Math.floor(a / nav.cols), x1 = b % nav.cols, y1 = Math.floor(b / nav.cols);
    var dx = x1 - x0, dy = y1 - y0;
    if (Math.min(Math.abs(dx), Math.abs(dy)) < 12 || Math.abs(dx / dy) < .6 || Math.abs(dx / dy) > 1.667) return false;
    var c1 = y0 * nav.cols + x1, c2 = y1 * nav.cols + x0;
    if ((lineClear(nav, a, c1) && lineClear(nav, c1, b)) || (lineClear(nav, a, c2) && lineClear(nav, c2, b)) || !lineClear(nav, a, b)) return false;
    for (var i = from + 1; i <= to; i++) {
      var previous = cells[i - 1], current = cells[i];
      if ((current % nav.cols - previous % nav.cols) * dx < 0 || (Math.floor(current / nav.cols) - Math.floor(previous / nav.cols)) * dy < 0) return false;
    }
    // Game-road labels also cover corridors, so a diagonal corridor is told apart by its width across the line.
    var samples = Math.max(Math.abs(dx), Math.abs(dy)), widths = [];
    for (var step = 1; step < samples; step++) {
      var spans = diagonalSpans(nav, x0 + dx * step / samples, y0 + dy * step / samples, dx, dy);
      if (spans[0] + spans[1] <= CORRIDOR_PX / nav.step) widths.push(spans[0] + spans[1]);
    }
    // A short cut across a wide right-angle elbow is not a slanted corridor.
    widths.sort(function(a, b) { return a - b; });
    return widths.length * 5 >= (samples - 1) * 4 && Math.sqrt(dx * dx + dy * dy) >= widths[Math.floor(widths.length / 2)] * 2.4;
  }
  // Distances from a point to the walls on both sides of the direction (dx, dy), in cells.
  function diagonalSpans(nav, x, y, dx, dy) {
    var length = Math.sqrt(dx * dx + dy * dy), nx = -dy / length, ny = dx / length, spans = [];
    for (var side = -1; side <= 1; side += 2) {
      var distance = 0;
      while (distance < 48) {
        var px = Math.round(x + nx * side * (distance + .5)), py = Math.round(y + ny * side * (distance + .5));
        if (px < 0 || py < 0 || px >= nav.cols || py >= nav.rows || !nav.edges[py * nav.cols + px]) break;
        distance += .5;
      }
      spans.push(distance);
    }
    return spans;
  }
  function location(nav, cell) {
    return { x: Math.min(nav.width - 1, cell % nav.cols * nav.step + nav.step / 2),
      y: Math.min(nav.height - 1, Math.floor(cell / nav.cols) * nav.step + nav.step / 2) };
  }
  function pointAt(nav, floor, x, y) {
    if (FLOORS.indexOf(floor) < 0 || !isFinite(x) || !isFinite(y) || x < 0 || y < 0 || x > nav.width || y > nav.height) return null;
    var col = Math.min(nav.cols - 1, Math.floor(x / nav.step)), row = Math.min(nav.rows - 1, Math.floor(y / nav.step));
    var cell = row * nav.cols + col, best = -1, distance = Infinity;
    var selectedRoom = (nav.selectionLabels || nav.labels)[cell];
    var exactRoom = selectedRoom && nav.rooms.filter(function(room) { return room.id === selectedRoom; })[0];
    if (exactRoom && nav.edges[exactRoom.cell]) return { floor: floor, cell: exactRoom.cell, kind: "room", roomId: exactRoom.id };
    var radius = Math.max(2, Math.ceil(9 / nav.step));
    for (var yy = Math.max(0, row - radius); yy <= Math.min(nav.rows - 1, row + radius); yy++) {
      for (var xx = Math.max(0, col - radius); xx <= Math.min(nav.cols - 1, col + radius); xx++) {
        var at = yy * nav.cols + xx;
        if (!nav.edges[at] || (nav.outlineStrips && nav.outlineStrips[at])) continue;
        var p = location(nav, at), d = Math.pow(p.x - x, 2) + Math.pow(p.y - y, 2);
        if (d < distance) { distance = d; best = at; }
      }
    }
    if (best < 0 || distance > 12 * 12) return null;
    var roomId = nav.labels[best], room = nav.rooms.filter(function(r) { return r.id === roomId; })[0];
    if (!room) {
      function reach(bit, delta) {
        var cursor = best, count = 0;
        while (count < 32 && (nav.edges[cursor] & bit) && !nav.labels[cursor + delta]) { cursor += delta; count++; }
        return count;
      }
      var left = reach(4, -1), right = reach(1, 1), up = reach(8, -nav.cols), down = reach(2, nav.cols);
      var horizontal = left + right, vertical = up + down;
      if (horizontal < vertical * .7) best += Math.round((right - left) / 2);
      else if (vertical < horizontal * .7) best += Math.round((down - up) / 2) * nav.cols;
    }
    return { floor: floor, cell: room ? room.cell : best, kind: room ? "room" : "via", roomId: room ? room.id : 0 };
  }
  function entranceAt(nav, floor, entries, x, y, radius) {
    if (FLOORS.indexOf(floor) < 0 || !isFinite(x) || !isFinite(y) || !(radius > 0)) return null;
    var best = null, distance = radius * radius;
    (entries || []).forEach(function(entry) {
      if (!Number.isInteger(entry.cell) || !nav.edges[entry.cell]) return;
      if (!isFinite(entry.x) || !isFinite(entry.y)) return;
      var b = entry.patch && (entry.patch.hitBounds || entry.patch.bounds);
      var inDoorway = b && x >= b[0] && x <= b[0] + b[2] && y >= b[1] && y <= b[1] + b[3];
      var d = inDoorway ? 0 : Math.pow(entry.x - x, 2) + Math.pow(entry.y - y, 2);
      if (d <= distance) { best = { floor: floor, cell: entry.cell, kind: "via", roomId: 0 }; distance = d; }
    });
    return best;
  }
  function selectionAt(nav, floor, entries, x, y, radius) {
    var point = pointAt(nav, floor, x, y), door = entranceAt(nav, floor, entries, x, y, radius);
    if (point && point.kind === "room" && door) {
      var entry = entries.filter(function(e) { return e.cell === door.cell; })[0];
      var half = Math.max(28, Math.max(nav.width, nav.height) * .025) / 2;
      var b = entry.patch && (entry.patch.hitBounds || entry.patch.bounds);
      var glyph = Math.abs(x - entry.x) <= half && Math.abs(y - entry.y) <= half;
      var doorway = b && x >= b[0] && x <= b[0] + b[2] && y >= b[1] && y <= b[1] + b[3];
      if (!glyph && !doorway) return point;
    }
    return door || point;
  }
  function markerPosition(nav, point) {
    if (point.cell < 0 && point.legacyPosition) return { x: point.legacyPosition.x, y: point.legacyPosition.y };
    var room = point.kind === "room" && nav.rooms.filter(function(r) { return r.id === point.roomId; })[0];
    return location(nav, room ? room.cell : point.cell);
  }
  function markerGroups(points, floor) {
    var groups = {}, ordered = [];
    points.forEach(function(point, index) {
      if (point.floor !== floor) return;
      var key = point.cell < 0 ? "unmatched:" + index : point.kind === "room" ? "room:" + point.roomId : "via:" + point.cell;
      if (!groups[key]) { groups[key] = { point: point, numbers: [] }; ordered.push(groups[key]); }
      groups[key].numbers.push(index + 1);
    });
    return ordered.map(function(group) {
      group.label = group.numbers.slice(0, 3).join("/") + (group.numbers.length > 3 ? "+" + (group.numbers.length - 3) : "");
      return group;
    });
  }
  function Heap() { this.items = []; }
  function before(a, b) {
    if (Math.abs(a.priority - b.priority) > 1e-9) return a.priority < b.priority;
    if (Math.abs((a.bias || 0) - (b.bias || 0)) > 1e-9) return (a.bias || 0) < (b.bias || 0);
    return a.index < b.index;
  }
  Heap.prototype.push = function(value) {
    var a = this.items, i = a.length; a.push(value);
    while (i > 0) { var p = (i - 1) >> 1; if (!before(value, a[p])) break; a[i] = a[p]; i = p; }
    a[i] = value;
  };
  Heap.prototype.pop = function() {
    var a = this.items, result = a[0], value = a.pop();
    if (!a.length) return result;
    var i = 0;
    while (true) {
      var l = i * 2 + 1, r = l + 1;
      if (l >= a.length) break;
      var c = r < a.length && before(a[r], a[l]) ? r : l;
      if (!before(a[c], value)) break;
      a[i] = a[c]; i = c;
    }
    a[i] = value; return result;
  };
  function validPoint(floors, point) {
    var nav = floors[point && point.floor];
    return !!nav && Number.isInteger(point.cell) && point.cell >= 0 && point.cell < nav.size && !!nav.edges[point.cell];
  }
  function floorAssociationEnabled(group) {
    var policy = group && group.plannerPolicy;
    return !!group && group.kind === "floor-association" && group.association === "same-source-room-instance" &&
      group.geometryExplicit === true && policy && policy.id === "bidirectional-source-door-sets-v1" &&
      policy.enabled === true && policy.direction === "bidirectional" &&
      ["b1", "b2", "b3"].indexOf(group.fromFloor) >= 0 && ["b1", "b2", "b3"].indexOf(group.toFloor) >= 0 && group.fromFloor !== group.toFloor &&
      Array.isArray(group.fromPorts) && group.fromPorts.length > 0 && Array.isArray(group.toPorts) && group.toPorts.length > 0 &&
      typeof group.levelCost === "number" && isFinite(group.levelCost) && group.levelCost > 0;
  }
  function transitionsForTemplate(template) {
    var result = template.stairsVerified ? (template.stairs || []).filter(function(s) { return s.verified === true && s.kind === "stairs"; }) : [];
    if (template.scenario === "phosphorescence" && /^season-s1-10[67][0-9]{3}$/.test(template.id) && template.floorAssociationsReady === true && /^[a-f0-9]{64}$/.test(template.floorAssociationSignature || "")) {
      result = result.concat((template.floorAssociations || []).filter(floorAssociationEnabled));
    }
    return result;
  }
  function findPath(floors, stairs, start, end) {
    if (!validPoint(floors, start) || !validPoint(floors, end)) throw Error("目标不在已确认的可走区域内");
    var ids = FLOORS.filter(function(f) { return !!floors[f]; }), offsets = {}, total = 0, links = {}, portals = {};
    ids.forEach(function(f) { offsets[f] = total; total += floors[f].size; });
    (stairs || []).forEach(function(s) {
      if (floorAssociationEnabled(s)) {
        function validPorts(ports, floor) {
          return ports.every(function(p) { return p.floor === floor && validPoint(floors, p) && Array.isArray(p.mapPlaneXZ) &&
            p.mapPlaneXZ.length === 2 && p.mapPlaneXZ.every(function(v) { return typeof v === "number" && isFinite(v); }); });
        }
        if (!validPorts(s.fromPorts, s.fromFloor) || !validPorts(s.toPorts, s.toFloor)) return;
        // Keep the source door sets as one room association. Entry/exit selection is
        // a 2D editor policy, not a claim that every pair is a game navigation link.
        function attach(fromPorts, toPorts) {
          fromPorts.forEach(function(p) {
            var at = offsets[p.floor] + p.cell; portals[at] = true;
            (links[at] || (links[at] = [])).push({ port: p, targets: toPorts, levelCost: s.levelCost });
          });
        }
        attach(s.fromPorts, s.toPorts); attach(s.toPorts, s.fromPorts); return;
      }
      if (s.verified !== true || s.kind !== "stairs" || !validPoint(floors, s.from) || !validPoint(floors, s.to)) return;
      var a = offsets[s.from.floor] + s.from.cell, b = offsets[s.to.floor] + s.to.cell, cost = Number(s.cost);
      if (!(cost > 0) || !isFinite(cost)) return;
      (links[a] || (links[a] = [])).push({ index: b, cost: cost });
      if (s.bidirectional !== false) (links[b] || (links[b] = [])).push({ index: a, cost: cost });
    });
    if (start.floor !== end.floor && !Object.keys(links).length) throw Error("这张图的跨层楼梯尚未核实，暂时只能规划同层路线");
    var hasLinks = Object.keys(links).length > 0;
    var from = offsets[start.floor] + start.cell, to = offsets[end.floor] + end.cell;
    var distance = new Float64Array(total), travel = new Float64Array(total), preference = new Float64Array(total), parent = new Int32Array(total), closed = new Uint8Array(total);
    distance.fill(Infinity); preference.fill(Infinity); parent.fill(-1); distance[from] = 0; preference[from] = 0;
    var heap = new Heap(); heap.push({ index: from, priority: 0, cost: 0 });
    function domain(index) {
      for (var k = ids.length - 1; k >= 0; k--) if (index >= offsets[ids[k]]) return ids[k];
      throw Error("导航节点错误");
    }
    function heuristic(index) {
      if (hasLinks || start.floor !== end.floor) return 0;
      var nav = floors[start.floor], cell = index - offsets[start.floor];
      return Math.hypot(cell % nav.cols - end.cell % nav.cols, Math.floor(cell / nav.cols) - Math.floor(end.cell / nav.cols)) * nav.step * nav.unitsPerPixel;
    }
    function visit(next, at, cost, linkedFloor) {
      var nextFloor = linkedFloor || floorId, nextNav = linkedFloor ? floors[nextFloor] : nav, nextLocal = next - offsets[nextFloor];
      if (next !== to && !portals[next] && nextNav.outlineStrips && nextNav.outlineStrips[nextLocal]) return;
      var wallCost = !linkedFloor && nav.walkingCosts ? (nav.walkingCosts[nextLocal] + nav.walkingCosts[local]) / 2 : 0;
      var candidate = distance[at] + cost * (1 + wallCost), gap = (nextNav.clearance ? nextNav.clearance[nextLocal] / 3 : 1) + .25;
      var bias = preference[at] + 1 / (gap * gap);
      if (!closed[next] && (candidate < distance[next] - 1e-9 ||
          (Math.abs(candidate - distance[next]) <= 1e-9 && bias < preference[next] - 1e-9))) {
        distance[next] = candidate; travel[next] = travel[at] + cost; preference[next] = bias; parent[next] = at;
        heap.push({ index: next, cost: candidate, bias: bias, priority: candidate + heuristic(next) });
      }
    }
    while (heap.items.length) {
      var current = heap.pop(), at = current.index;
      if (closed[at] || current.cost !== distance[at] || (current.bias || 0) !== preference[at]) continue;
      if (at === to) {
        var path = [], cursor = at;
        while (cursor !== -1) { var floor = domain(cursor); path.push({ floor: floor, cell: cursor - offsets[floor] }); cursor = parent[cursor]; }
        return { cells: path.reverse(), cost: travel[at] };
      }
      closed[at] = 1;
      var floorId = domain(at), nav = floors[floorId], local = at - offsets[floorId], mask = nav.edges[local], cost = nav.step * nav.unitsPerPixel;
      if ((mask & 1) && local % nav.cols < nav.cols - 1) visit(at + 1, at, cost);
      if ((mask & 2) && local + nav.cols < nav.size) visit(at + nav.cols, at, cost);
      if ((mask & 4) && local % nav.cols > 0) visit(at - 1, at, cost);
      if ((mask & 8) && local >= nav.cols) visit(at - nav.cols, at, cost);
      if (mask & 16) visit(at - nav.cols + 1, at, cost * Math.SQRT2);
      if (mask & 32) visit(at + nav.cols + 1, at, cost * Math.SQRT2);
      if (mask & 64) visit(at + nav.cols - 1, at, cost * Math.SQRT2);
      if (mask & 128) visit(at - nav.cols - 1, at, cost * Math.SQRT2);
      (links[at] || []).forEach(function(link) {
        if (link.targets) link.targets.forEach(function(target) {
          var index = offsets[target.floor] + target.cell;
          visit(index, at, Math.hypot(target.mapPlaneXZ[0] - link.port.mapPlaneXZ[0], target.mapPlaneXZ[1] - link.port.mapPlaneXZ[1], link.levelCost), target.floor);
        });
        else visit(link.index, at, link.cost, domain(link.index));
      });
    }
    throw Error("这两个位置之间没有已确认的通路，请调整目标或必经道路");
  }
  function plan(floors, stairs, points) {
    if (!Array.isArray(points) || points.length > MAX_POINTS) throw Error("一张地图最多添加" + MAX_POINTS + "个点");
    var segments = [], total = 0;
    points.forEach(function(p) {
      if (p.start != null && typeof p.start !== "boolean") throw Error("起点标记无效");
      if (p.color != null && !/^#[0-9a-f]{6}$/i.test(p.color)) throw Error("线段颜色无效");
    });
    for (var i = 1; i < points.length; i++) {
      if (points[i].start === true) continue;
      var result;
      try { result = findPath(floors, stairs, points[i - 1], points[i]); }
      catch (error) { throw Error("第" + i + "段：" + error.message); }
      total += result.cost;
      segments.push({ from: i - 1, to: i, cells: result.cells, cost: result.cost, color: points[i].color });
    }
    return { segments: segments, cost: total };
  }
  // Plans saved on an earlier road version keep their targets: rooms follow the shipped
  // position table, other points move to the nearest walkable cell, then the route is replanned.
  var SNAP_RADIUS = 12;
  function snapCell(nav, cell) {
    if (!Number.isInteger(cell) || cell < 0 || cell >= nav.size) return -1;
    if (nav.edges[cell]) return cell;
    var x0 = cell % nav.cols, y0 = Math.floor(cell / nav.cols), best = -1, distance = Infinity;
    for (var y = Math.max(0, y0 - SNAP_RADIUS); y <= Math.min(nav.rows - 1, y0 + SNAP_RADIUS); y++) {
      for (var x = Math.max(0, x0 - SNAP_RADIUS); x <= Math.min(nav.cols - 1, x0 + SNAP_RADIUS); x++) {
        var at = y * nav.cols + x, d = (x - x0) * (x - x0) + (y - y0) * (y - y0);
        if (nav.edges[at] && d <= SNAP_RADIUS * SNAP_RADIUS && d < distance) { distance = d; best = at; }
      }
    }
    return best;
  }
  function migratePoints(template, migration, floors, points) {
    var table = migration && migration.version === 1 && migration.templates && migration.templates[template.id];
    if (!table) throw Error("缺少当前底图的道路对应数据");
    var mapped = [], lost = [], carryStart = false;
    points.forEach(function(p, index) {
      var nav = floors[p.floor], rooms = table.floors[p.floor] && table.floors[p.floor].rooms, next = null;
      if (nav && rooms) {
        var room = p.kind === "room" ? rooms[String(p.roomId)] : null, cell = room ? room[1] : snapCell(nav, p.cell);
        if (cell >= 0 && nav.edges[cell]) next = { floor: p.floor, cell: cell, kind: room && room[0] ? "room" : "via", roomId: room ? room[0] : 0 };
      }
      if (!next) { lost.push(index + 1); carryStart = carryStart || p.start === true; return; }
      if (p.start === true || carryStart) next.start = true;
      if (p.color != null) next.color = p.color;
      carryStart = false; mapped.push(next);
    });
    return { points: mapped, lost: lost };
  }
  function migrate(template, migration, floors, saved) {
    if (saved.navigationSignature === template.navigationSignature) return saved;
    if ((template.compatibleNavigationSignatures || []).indexOf(saved.navigationSignature) < 0) throw Error("路线底图版本需要更新");
    var result = migratePoints(template, migration, floors, saved.points);
    if (result.lost.length) throw Error("第" + result.lost.join("、") + "个目标在当前地图上没有可走位置，请在编辑器中重新点选");
    var route = plan(floors, transitionsForTemplate(template), result.points);
    var next = JSON.parse(JSON.stringify(saved));
    next.navigationSignature = template.navigationSignature;
    next.points = result.points.map(function(p) {
      var point = { floor: p.floor, cell: p.cell, kind: p.kind, roomId: p.roomId };
      if (saved.version === 2) { if (p.start === true) point.start = true; if (p.color != null) point.color = p.color; }
      return point;
    });
    next.segments = document(template, result.points, route, floors).segments;
    return next;
  }
  function floorSegments(route, floor, fallbackColor) {
    var paths = [];
    (route.segments || []).forEach(function(segment) {
      var current = [];
      function flush() {
        if (current.length) paths.push({ cells: current, color: segment.color || fallbackColor || "#63CAFF", from: segment.from, to: segment.to });
        current = [];
      }
      segment.cells.forEach(function(p) { if (p.floor === floor) current.push(p.cell); else flush(); });
      flush();
    });
    return paths;
  }
  function floorPaths(route, floor) { return floorSegments(route, floor).map(function(p) { return p.cells; }); }
  function ribbonEdges(nav, opaque) {
    var edges = new Uint8Array(nav.cols * nav.rows);
    for (var at = 0; at < edges.length; at++) if (opaque[at]) {
      if (at % nav.cols < nav.cols - 1 && opaque[at + 1]) edges[at] |= 1;
      if (at + nav.cols < edges.length && opaque[at + nav.cols]) edges[at] |= 2;
      if (at % nav.cols && opaque[at - 1]) edges[at] |= 4;
      if (at >= nav.cols && opaque[at - nav.cols]) edges[at] |= 8;
    }
    return edges;
  }
  // Paint-footprint edges never replace the validated walking graph.
  function strokePaths(nav, paths, width, paintEdges) {
    if (!(width > 0) || !isFinite(width)) throw Error("路线线宽无效");
    var groups = {}, pieces = [], chains = [], backdrops = [], colors = [];
    function gcd(a, b) { while (b) { var r = a % b; a = b; b = r; } return a; }
    function mix(a, b, t) { return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t }; }
    function same(a, b) { return Math.hypot(a.x - b.x, a.y - b.y) < 1e-6; }
    function cell(p) { return p.x < 0 || p.y < 0 || p.x >= nav.width || p.y >= nav.height ? -1 : Math.floor(p.y / nav.step) * nav.cols + Math.floor(p.x / nav.step); }
    var footprint = paintEdges ? Object.assign({}, nav, { edges: paintEdges }) : nav;
    function clear(a, b) { return lineClear(footprint, cell(a), cell(b)); }
    paths.forEach(function(path, index) {
      for (var j = 1; j < path.vertices.length; j++) {
        var a = path.vertices[j - 1], b = path.vertices[j], ax = a % nav.cols, ay = Math.floor(a / nav.cols), bx = b % nav.cols, by = Math.floor(b / nav.cols);
        var dx = bx - ax, dy = by - ay, divisor = gcd(Math.abs(dx), Math.abs(dy));
        if (!divisor) continue;
        dx /= divisor; dy /= divisor; if (dx < 0 || (!dx && dy < 0)) { dx = -dx; dy = -dy; }
        var key = dx + "," + dy + ":" + (dx * ay - dy * ax), from = dx * ax + dy * ay, to = dx * bx + dy * by;
        (groups[key] || (groups[key] = [])).push({ path: index, ordinal: j - 1, from: from, to: to, low: Math.min(from, to), high: Math.max(from, to), a: location(nav, a), b: location(nav, b) });
      }
    });
    Object.keys(groups).forEach(function(key) {
      var lines = groups[key], cuts = [];
      lines.forEach(function(e) { cuts.push(e.low, e.high); });
      cuts.sort(function(a, b) { return a - b; }); cuts = cuts.filter(function(x, i) { return !i || x !== cuts[i - 1]; });
      for (var i = 1; i < cuts.length; i++) {
        var midpoint = (cuts[i - 1] + cuts[i]) / 2, users = lines.filter(function(e) { return e.low < midpoint && e.high > midpoint; });
        if (!users.length) continue;
        var primary = users[0], start = primary.to > primary.from ? cuts[i - 1] : cuts[i], end = primary.to > primary.from ? cuts[i] : cuts[i - 1];
        var t = (start - primary.from) / (primary.to - primary.from);
        pieces.push({ primary: primary.path, ordinal: primary.ordinal, at: t, members: users.map(function(e) { return e.path; }), a: mix(primary.a, primary.b, t), b: mix(primary.a, primary.b, (end - primary.from) / (primary.to - primary.from)) });
      }
    });
    pieces.sort(function(a, b) { return a.primary - b.primary || a.ordinal - b.ordinal || a.at - b.at; });
    pieces.forEach(function(piece) {
      var previous = chains[chains.length - 1], signature = piece.members.join(",");
      if (previous && previous.primary === piece.primary && previous.signature === signature && same(previous.vertices[previous.vertices.length - 1], piece.a)) previous.vertices.push(piece.b);
      else chains.push({ primary: piece.primary, signature: signature, members: piece.members, vertices: [piece.a, piece.b] });
    });
    function normal(a, b) { var dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy); return { x: -dy / length, y: dx / length }; }
    function shifted(p, n, amount) { return { x: p.x + n.x * amount, y: p.y + n.y * amount }; }
    function available(p, n, desired) {
      function extent(sign) {
        var low = 0, high = desired * 2;
        if (clear(p, shifted(p, n, sign * high))) return high;
        for (var i = 0; i < 8; i++) { var mid = (low + high) / 2; if (clear(p, shifted(p, n, sign * mid))) low = mid; else high = mid; }
        return Math.max(0, low - nav.step / 8);
      }
      var low = -extent(-1), high = extent(1);
      return { low: low, high: high, half: Math.min(desired, (high - low) / 2) };
    }
    function simplify(points) {
      var result = [];
      points.forEach(function(p) {
        if (result.length && same(result[result.length - 1], p)) return;
        while (result.length > 1) {
          var a = result[result.length - 2], b = result[result.length - 1], dx = b.x - a.x, dy = b.y - a.y, ex = p.x - b.x, ey = p.y - b.y;
          if (Math.abs(dx * ey - dy * ex) > 1e-7 || dx * ex + dy * ey < 0) break;
          result.pop();
        }
        result.push(p);
      });
      return result;
    }
    chains.forEach(function(chain) {
      var vertices = chain.vertices, count = chain.members.length, desired = width * Math.min(2, 1 + (count - 1) * .75) / 2;
      var normals = [], miters = [], samples = [], distance = 0;
      for (var i = 1; i < vertices.length; i++) normals.push(normal(vertices[i - 1], vertices[i]));
      for (var i = 0; i < vertices.length; i++) {
        var n = i === 0 ? normals[0] : normals[i - 1];
        if (i > 0 && i < vertices.length - 1) {
          var next = normals[i], denominator = 1 + n.x * next.x + n.y * next.y;
          if (denominator > .05) {
            n = { x: (n.x + next.x) / denominator, y: (n.y + next.y) / denominator };
            var length = Math.hypot(n.x, n.y); if (length > 2) { n.x *= 2 / length; n.y *= 2 / length; }
          }
        }
        miters.push(n);
      }
      for (var segment = 1; segment < vertices.length; segment++) {
        var a = vertices[segment - 1], b = vertices[segment], length = Math.hypot(b.x - a.x, b.y - a.y), steps = Math.max(1, Math.ceil(length / nav.step - 1e-8));
        for (var i = segment === 1 ? 0 : 1; i <= steps; i++) {
          var p = mix(a, b, i / steps), n = i === 0 ? miters[segment - 1] : i === steps ? miters[segment] : normals[segment - 1];
          var span = available(p, n, desired);
          samples.push({ p: p, n: n, distance: distance + length * i / steps, half: span.half, low: span.low, high: span.high, offset: 0 });
        }
        distance += length;
      }
      if (samples.length < 2) return;
      samples[0].half = Math.min(samples[0].half, width / 2); samples[samples.length - 1].half = Math.min(samples[samples.length - 1].half, width / 2);
      for (var i = 1; i < samples.length; i++) samples[i].half = Math.min(samples[i].half, samples[i - 1].half + (samples[i].distance - samples[i - 1].distance) * .5);
      for (var i = samples.length - 2; i >= 0; i--) samples[i].half = Math.min(samples[i].half, samples[i + 1].half + (samples[i + 1].distance - samples[i].distance) * .5);
      function within(s, value) { return Math.max(s.low + s.half, Math.min(s.high - s.half, value)); }
      samples.forEach(function(s) { s.offset = within(s, 0); });
      for (var i = samples.length - 2; i >= 0; i--) {
        var limit = (samples[i + 1].distance - samples[i].distance) * .5;
        samples[i].offset = within(samples[i], Math.max(samples[i + 1].offset - limit, Math.min(samples[i + 1].offset + limit, samples[i].offset)));
      }
      for (var i = 1; i < samples.length; i++) {
        var limit = (samples[i].distance - samples[i - 1].distance) * .5;
        samples[i].offset = within(samples[i], Math.max(samples[i - 1].offset - limit, Math.min(samples[i - 1].offset + limit, samples[i].offset)));
      }
      function polygon(lane) {
        var left = [], right = [], widest = 0;
        samples.forEach(function(s) {
          var band = s.half * 2, gap = count > 1 ? Math.min(width * .1, band * .15 / (count - 1)) : 0, channel = (band - gap * (count - 1)) / count;
          var low = lane < 0 ? s.offset - s.half : s.offset - s.half + lane * (channel + gap), high = lane < 0 ? s.offset + s.half : low + channel;
          left.push(shifted(s.p, s.n, low)); right.push(shifted(s.p, s.n, high)); widest = Math.max(widest, high - low);
        });
        return { points: simplify(left).concat(simplify(right).reverse()), color: lane < 0 ? "#172735" : paths[chain.members[lane]].color, width: widest, laneCount: count, filled: true, separator: lane < 0 };
      }
      if (count > 1) backdrops.push(polygon(-1));
      for (var lane = 0; lane < count; lane++) colors.push(polygon(lane));
    });
    return backdrops.concat(colors);
  }
  function document(template, points, route, floors, style) {
    if (!template || !/^[a-f0-9]{64}$/.test(template.navigationSignature || "")) throw Error("底图版本数据不完整，请重新打开地图");
    var version = style && style.version === 2 || points.some(function(p) { return p.start === true || p.color != null; }) ? 2 : 1;
    var result = { version: version, templateId: template.id, navigationVersion: 1, navigationSignature: template.navigationSignature,
      points: points.map(function(p) {
        var point = { floor: p.floor, cell: p.cell, kind: p.kind, roomId: p.roomId };
        if (version === 2) {
          if (p.start === true) point.start = true;
          if (p.color != null) {
            if (!/^#[0-9a-f]{6}$/i.test(p.color)) throw Error("线段颜色无效");
            point.color = p.color.toUpperCase();
          }
        }
        return point;
      }),
      segments: route.segments.map(function(segment) {
        var parts = [], part = null, previous = null;
        segment.cells.forEach(function(p) {
          if (!part || part.floor !== p.floor) { part = { floor: p.floor, cell: p.cell, runs: [] }; parts.push(part); }
          else {
            var delta = p.cell - previous.cell, nav = floors[p.floor];
            var direction = delta === 1 ? 1 : delta === nav.cols ? 2 : delta === -1 ? 4 : delta === -nav.cols ? 8 :
              delta === 1 - nav.cols ? 16 : delta === nav.cols + 1 ? 32 : delta === nav.cols - 1 ? 64 : delta === -nav.cols - 1 ? 128 : 0;
            if (!direction) throw Error("路线包含未连接的道路");
            var runs = part.runs;
            if (runs.length && runs[runs.length - 2] === direction) runs[runs.length - 1]++;
            else runs.push(direction, 1);
          }
          previous = p;
        });
        return { from: segment.from, to: segment.to, parts: parts };
      })
    };
    if (style) {
      if (!/^#[0-9a-f]{6}$/i.test(style.lineColor || "")) throw Error("请输入有效的六位颜色值");
      if (FLOORS.indexOf(style.editorFloor) < 0) throw Error("编辑楼层无效");
      result.lineColor = style.lineColor.toUpperCase(); result.editorFloor = style.editorFloor;
    }
    if (template.scenario === "phosphorescence" && /^[a-f0-9]{64}$/.test(template.floorAssociationSignature || "")) result.floorAssociationSignature = template.floorAssociationSignature;
    return result;
  }
  return { floors: FLOORS, prepare: prepare, pointAt: pointAt, location: location, findPath: findPath, plan: plan, transitionsForTemplate: transitionsForTemplate, floorPaths: floorPaths, floorSegments: floorSegments, document: document,
    snapCell: snapCell, migratePoints: migratePoints, migrate: migrate,
    lineClear: lineClear, straightPath: straightPath, entranceAt: entranceAt, selectionAt: selectionAt, markerPosition: markerPosition, markerGroups: markerGroups, strokePaths: strokePaths, ribbonEdges: ribbonEdges };
});
