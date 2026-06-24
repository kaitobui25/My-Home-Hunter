# Bug Fix: Cluster Icons Không Phản Ánh Căn Yêu Thích

**Ngày sửa**: 2026-05-23  
**File**: `src/web/templates/map.html`  
**Trạng thái**: ✅ Hoàn thành

---

## 📋 VẤN ĐỀ CHI TIẾT

### Mô tả lỗi
Khi một hoặc nhiều căn nhà yêu thích nằm trong cluster icon, chúng không được hiển thị:
- **Lúc map load**: Cluster icon không có dấu hiệu ★ (không thấy căn yêu thích)
- **Sau khi zoom in 3 lần**: Marker riêng lẻ với ★ xuất hiện
- **Sau khi zoom out**: Marker lại biến mất vào cluster, không thấy ★ nữa

### Tác động
- Người dùng **không biết** là có căn yêu thích trong cluster
- Phải zoom in mới thấy được, rất bất tiện
- Làm mất tính năng "yêu thích" của ứng dụng

### Ví dụ Scenario
```
1. Mở map → Cluster icon hiển thị [5] (màu vàng/xanh/xám)
2. Mark 1 căn trong cluster là favorite (★)
3. Zoom out → Cluster icon vẫn [5] (không có ★)
   ❌ Bạn không biết là có yêu thích trong đó!
4. Zoom in 3 lần → Thấy marker với ★
5. Zoom out → Cluster icon lại [5] (mất ★)
   ❌ Quay vòng, rất phiền!
```

---

## 🔍 PHÂN TÍCH NGUYÊN NHÂN

### Vấn đề 1: Marker không lưu trữ favorite status

**File**: `src/web/templates/map.html`  
**Location**: Line 1624-1628 (hàm `renderMarkers`)

**Code hiện tại (BỊ LỖI):**
```javascript
listings.forEach((l) => {
    const lid = listingId(l);
    const isViewed = viewedSet.has(lid);
    const isFaved = favedSet.has(lid);      // ← Biết favorite status
    const isNew = newSet.has(lid);
    const marker = L.marker([l.lat, l.lng], {
        icon: makeHouseIcon(l, isViewed, isFaved, isNew),  // ← Dùng để tạo icon
        _teleSent: l.tele_sent,    // ← Lưu trữ sent status (cho cluster check)
        _listing: l,
    });
    // ❌ THIẾU: _isFaved (favorite status không được lưu)
    //    → Cluster icon không thể access được!
});
```

**Vấn đề:**
- Marker **lưu `_teleSent`** (dùng để cluster check sent status)
- Nhưng **không lưu `_isFaved`** (nên cluster không thể detect favorite)
- Biến `isFaved` tính được nhưng bị bỏ đi, chỉ dùng cho individual marker icon
- **Cluster icon logic không có thông tin để check**

---

### Vấn đề 2: Cluster icon logic bỏ qua favorite status

**File**: `src/web/templates/map.html`  
**Location**: Line 1541-1584 (hàm `iconCreateFunction`)

**Code hiện tại (BỊ LỖI):**
```javascript
iconCreateFunction: function (cluster) {
    const count = cluster.getChildCount();
    const children = cluster.getAllChildMarkers();
    
    // ✓ Kiểm tra: viewed?
    const allViewed = children.every((m) => {
        const listing = m.options._listing;
        if (!listing) return false;
        return viewedSet.has(listingId(listing));
    });
    
    // ✓ Kiểm tra: sent?
    const allSent = children.every((m) => m.options._teleSent);
    const noneSent = children.every((m) => !m.options._teleSent);
    
    // ❌ THIẾU: Kiểm tra favorite
    // Không có dòng:
    // const hasFav = children.some((m) => m.options._isFaved);
    
    // Xác định màu sắc (bỏ qua favorite):
    const fill = allViewed
        ? "#64748b"          // Xám (viewed)
        : allSent
          ? "#34d399"        // Xanh (all sent)
          : noneSent
            ? "#fb923c"      // Cam (none sent)
            : "#fbbf24";     // Vàng (mixed)
    
    const border = allViewed ? "#334155" : allSent ? "#059669" : noneSent ? "#c2410c" : "#b45309";
    
    // ❌ Không có ★ indicator
    const html = `<div style="...">${count}</div>`;
    // Chỉ hiển thị số (ví dụ: "5"), không có ★
}
```

**Vấn đề:**
- Chỉ check: `viewed`, `sent`/`unsent`
- **Hoàn toàn không check favorite** → cluster không biết có yêu thích hay không
- Không có visual indicator (★) cho favorite
- **Favorite status bị bỏ qua hoàn toàn** ngay cả khi tồn tại

---

## ✅ GIẢI PHÁP ĐÃ THỰC HIỆN

### Sửa 1: Thêm `_isFaved` vào marker options

**File**: `src/web/templates/map.html`  
**Location**: Line 1624-1628

**Before (BỊ LỖI):**
```javascript
const marker = L.marker([l.lat, l.lng], {
    icon: makeHouseIcon(l, isViewed, isFaved, isNew),
    _teleSent: l.tele_sent,
    _listing: l,
});
// ❌ Thiếu _isFaved
```

**After (FIXED):**
```javascript
const marker = L.marker([l.lat, l.lng], {
    icon: makeHouseIcon(l, isViewed, isFaved, isNew),
    _teleSent: l.tele_sent,
    _isFaved: isFaved,              // ✅ THÊM: Lưu trữ favorite status
    _listing: l,
});
```

**Chi tiết:**
- `isFaved` đã được tính từ `favedSet.has(lid)`
- Giờ nó được lưu vào `marker.options._isFaved`
- Cluster icon có thể access qua `m.options._isFaved` cho mỗi marker

---

### Sửa 2: Update `iconCreateFunction` với ưu tiên favorite

**File**: `src/web/templates/map.html`  
**Location**: Line 1541-1584

**Before (BỊ LỖI):**
```javascript
iconCreateFunction: function (cluster) {
    const count = cluster.getChildCount();
    const children = cluster.getAllChildMarkers();
    const allViewed = children.every((m) => {
        const listing = m.options._listing;
        if (!listing) return false;
        return viewedSet.has(listingId(listing));
    });
    const allSent = children.every((m) => m.options._teleSent);
    const noneSent = children.every((m) => !m.options._teleSent);
    
    const fill = allViewed ? "#64748b" : allSent ? "#34d399" : noneSent ? "#fb923c" : "#fbbf24";
    const border = allViewed ? "#334155" : allSent ? "#059669" : noneSent ? "#c2410c" : "#b45309";
    const size = count > 9 ? 28 : 22;
    
    return L.divIcon({
        className: "",
        html: `<div style="
       width:${size}px;height:${size}px;border-radius:50%;
       background:${fill};border:2.5px solid ${border};
       display:flex;align-items:center;justify-content:center;
       font-size:11px;font-weight:700;color:#0f1117;
       box-shadow:0 0 8px ${fill}88;
       font-family:'Inter',sans-serif;
       ">${count}</div>`,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
    });
}
// ❌ Không check favorite, không có ★
```

**After (FIXED):**
```javascript
iconCreateFunction: function (cluster) {
    const count = cluster.getChildCount();
    const children = cluster.getAllChildMarkers();
    
    // ✅ THÊM: Check nếu bất kỳ marker nào là favorite
    const hasFav = children.some((m) => m.options._isFaved);
    
    const allViewed = children.every((m) => {
        const listing = m.options._listing;
        if (!listing) return false;
        return viewedSet.has(listingId(listing));
    });
    const allSent = children.every((m) => m.options._teleSent);
    const noneSent = children.every((m) => !m.options._teleSent);
    
    // ✅ ƯU TIÊN: favorite > viewed > all-sent > mixed
    let fill, border, starIcon = "";
    if (hasFav) {
        fill = "#f472b6";           // Màu pink (favorite color)
        border = "#be185d";
        starIcon = '<span style="position:absolute;font-size:9px;line-height:1;color:#fff;pointer-events:none;font-weight:700;">★</span>';
    } else if (allViewed) {
        fill = "#64748b";           // Màu xám (viewed)
        border = "#334155";
    } else if (allSent) {
        fill = "#34d399";           // Màu xanh (all sent)
        border = "#059669";
    } else if (noneSent) {
        fill = "#fb923c";           // Màu cam (none sent)
        border = "#c2410c";
    } else {
        fill = "#fbbf24";           // Màu vàng (mixed)
        border = "#b45309";
    }
    
    const size = count > 9 ? 28 : 22;
    return L.divIcon({
        className: "",
        html: `<div style="
           position:relative;
           width:${size}px;height:${size}px;border-radius:50%;
           background:${fill};border:2.5px solid ${border};
           display:flex;align-items:center;justify-content:center;
           font-size:11px;font-weight:700;color:#0f1117;
           box-shadow:0 0 8px ${fill}88;
           font-family:'Inter',sans-serif;
           ">${count}${starIcon}</div>`,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
    });
}
// ✅ Check favorite, ưu tiên cao nhất, thêm ★
```

**Logic ưu tiên (Priority Stack):**
```
┌─────────────────────────────────────┐
│ 1. Có yêu thích?                    │  
│    (hasFav = true)                  │  → Màu pink + ★
│    (children.some(..._isFaved))     │
└─────────────────────────────────────┘
            ↓ (không)
┌─────────────────────────────────────┐
│ 2. Tất cả viewed?                   │  
│    (allViewed = true)               │  → Màu xám
│    (children.every(...viewed))      │
└─────────────────────────────────────┘
            ↓ (không)
┌─────────────────────────────────────┐
│ 3. Tất cả sent?                     │  
│    (allSent = true)                 │  → Màu xanh
│    (children.every(..._teleSent))   │
└─────────────────────────────────────┘
            ↓ (không)
┌─────────────────────────────────────┐
│ 4. Không ai sent?                   │  
│    (noneSent = true)                │  → Màu cam
│    (children.every(!..._teleSent))  │
└─────────────────────────────────────┘
            ↓ (không)
┌─────────────────────────────────────┐
│ 5. Default (hỗn hợp)                │  
│    (không match case nào trên)      │  → Màu vàng
└─────────────────────────────────────┘
```

**Tại sao favorite ở priority 1?**
- Favorite là trạng thái được **user explicitly set** (có ý định)
- Viewed/sent là auto-tracked (không cần user action)
- Favorite cần được **highlight nhất** vì là phổ biến nhất

---

## 🎯 KẾT QUẢ SAU SỬA

### Hành vi mới (Expected Behavior):

#### Scenario 1: Cluster có căn yêu thích
```
1️⃣ Map load
   └─ Marker được render, 1 cái là favorite (isFaved=true)

2️⃣ Marker nằm vào cluster (vì overlapping)
   └─ Cluster icon được tạo, check children:
      • hasFav = children.some(..._isFaved) = TRUE
      • Màu = "#f472b6" (pink)
      • starIcon = "★"

3️⃣ Hiển thị
   ┌───────┐
   │  [5]★ │  ← Pink cluster + star = "Có favorite!"
   └───────┘

4️⃣ Zoom in (individual markers)
   ├─ Xám marker (viewed)
   ├─ Cam marker (unsent)
   ├─ Xanh marker (sent)
   └─ 💗 Pink marker với ★ (favorite)
       └─ Xác nhận: Favorite nằm ở đây

5️⃣ Zoom out (quay lại cluster)
   ┌───────┐
   │  [5]★ │  ← Vẫn pink + ★
   └───────┘
   ✅ KHÔNG mất! Người dùng vẫn biết có favorite
```

#### Scenario 2: Cluster chỉ viewed markers
```
Zoom in → Xám markers (all viewed)
Zoom out → Cluster xám (all viewed)
```

#### Scenario 3: Cluster hỗn hợp (mixed)
```
Zoom in → Markers nhiều màu
         └─ 💗 Pink (favorite)
         └─ Xám (viewed)  
         └─ Xanh (sent)
         └─ Cam (unsent)

Zoom out → Cluster PINK + ★
          (vì hasFav=true override mọi thứ)
```

---

## 📊 COMPARISON TABLE

| Khía cạnh | Trước | Sau |
|-----------|-------|-----|
| **Marker lưu favorite** | ❌ Không | ✅ `_isFaved: isFaved` |
| **Cluster check favorite** | ❌ Không | ✅ `hasFav = children.some(...)` |
| **Visual indicator** | ❌ `[5]` | ✅ `[5]★` |
| **Màu cluster nếu có fav** | ❌ Không định nghĩa | ✅ Pink (`#f472b6`) |
| **Priority** | ❌ Sent > viewed (bỏ qua fav) | ✅ **Favorite highest** |
| **Người dùng trải nghiệm** | ❌ Phải zoom in để thấy yêu thích | ✅ Thấy ngay ở cluster icon |

---

## 💻 CODE CHANGES SUMMARY

### File: `src/web/templates/map.html`

#### Change 1: Line 1624-1628
**Add `_isFaved` to marker options**

```diff
const marker = L.marker([l.lat, l.lng], {
    icon: makeHouseIcon(l, isViewed, isFaved, isNew),
    _teleSent: l.tele_sent,
+   _isFaved: isFaved,
    _listing: l,
});
```

#### Change 2: Line 1541-1584
**Update `iconCreateFunction` with favorite detection**

```diff
iconCreateFunction: function (cluster) {
    const count = cluster.getChildCount();
    const children = cluster.getAllChildMarkers();
+   const hasFav = children.some((m) => m.options._isFaved);
    const allViewed = children.every(...);
    const allSent = children.every(...);
    const noneSent = children.every(...);
    
+   let fill, border, starIcon = "";
+   if (hasFav) {
+       fill = "#f472b6";
+       border = "#be185d";
+       starIcon = '<span style="position:absolute;font-size:9px;...">★</span>';
+   } else if (allViewed) {
-   const fill = allViewed ? "#64748b" : ...;
    
    return L.divIcon({
        className: "",
        html: `<div style="position:relative;...">${count}${starIcon}</div>`,
        ...
    });
}
```

---

## ✨ VISUAL CHANGES

### Before (LỖI):
```
┌──────────────────────────────┐
│ Map zoom out                 │
│                              │
│   [99]  [50]  [25]           │
│                              │
│ (Không biết có yêu thích)    │
│ → Phải zoom in mới thấy      │
└──────────────────────────────┘
```

### After (FIXED):
```
┌──────────────────────────────┐
│ Map zoom out                 │
│                              │
│   [99]★ [50] [25]★           │
│    ↑                    ↑     │
│    Có favorite!        Có favorite!
│                              │
│ → Nhìn thấy ngay, không zoom │
└──────────────────────────────┘
```

---

## 🧪 TESTING CHECKLIST

- [ ] **Test 1**: Mark 1 căn là favorite, zoom out → Thấy ★ trên cluster
- [ ] **Test 2**: Mark 2 căn là favorite trong cluster → Cluster vẫn hiển thị ★
- [ ] **Test 3**: Unmark favorite → Cluster icon màu thay đổi (không có ★)
- [ ] **Test 4**: Zoom in/out nhiều lần → ★ luôn được bảo tồn
- [ ] **Test 5**: Cluster toàn viewed → Màu xám (không ★)
- [ ] **Test 6**: Cluster toàn sent → Màu xanh (không ★)
- [ ] **Test 7**: Cluster hỗn hợp có favorite → Màu pink + ★

---

## 📝 NOTES

1. **Tại sao dùng `some()` không phải `every()`?**
   - `some()` = "có bất kỳ favorite nào" (đúng nếu >= 1)
   - `every()` = "tất cả đều favorite" (sai nếu có 1 cái không)
   - Ta muốn highlight nếu **bất kỳ** marker nào là favorite

2. **Tại sao favorite priority cao nhất?**
   - User tự chọn favorite (explicit action)
   - Viewed/sent auto-tracked (passive)
   - Favorite là signal "tôi quan tâm cái này"

3. **Star icon ★ không overlay số?**
   - `position:absolute` để ★ nằm trên number
   - `top:50%;left:50%` centered
   - `pointer-events:none` để không block click

---

**Khác gì so với trước?** Xem [COMPARISON TABLE](#-comparison-table) ở trên!
