(() => {
    "use strict";

    const escapeHtml = (value) =>
        String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");

    const listingTooltip = (listing) => {
        const name = escapeHtml(listing?.name || "物件名不明");
        const price = listing?.price_man_yen != null
            ? `${Number(listing.price_man_yen).toFixed(1)}万円`
            : "価格不明";
        const distance = listing?.distance_km != null
            ? `${Number(listing.distance_km).toFixed(2)}km`
            : "距離不明";
        return `<strong>${name}</strong><br>${price} · ${distance}<br><span style="opacity:.65">クリックで詳細</span>`;
    };

    const keepPopupStableOnIconChange = (marker) => {
        if (marker._homeHunterStablePopup) return;
        marker._homeHunterStablePopup = true;

        const setIconNow = marker.setIcon.bind(marker);
        let pendingIcon = null;

        marker.setIcon = function (icon) {
            if (this.isPopupOpen?.()) {
                pendingIcon = icon;
                return this;
            }
            return setIconNow(icon);
        };

        marker.on("popupclose", () => {
            if (!pendingIcon) return;
            const icon = pendingIcon;
            pendingIcon = null;
            setIconNow(icon);
        });
    };

    const makePopupClickOnly = (marker, listing) => {
        // Remove the original openPopup-on-mouseover handler. Leaflet's normal
        // bindPopup click handler remains intact.
        marker.off("mouseover");
        if (listing) {
            marker.unbindTooltip();
            marker.bindTooltip(listingTooltip(listing), {
                direction: "top",
                sticky: true,
                opacity: 0.94,
                offset: [0, -8],
            });
        }
        marker.on("mouseover", function () {
            this.setZIndexOffset(1000);
        });
        marker.on("mouseout", function () {
            this.setZIndexOffset(0);
        });
    };

    if (typeof renderMarkers === "function") {
        const originalRenderMarkers = renderMarkers;
        renderMarkers = function (listings) {
            originalRenderMarkers(listings);
            allMarkers.forEach((marker) => {
                keepPopupStableOnIconChange(marker);
                makePopupClickOnly(marker, marker.options?._listing);
            });
        };
    }

    if (typeof renderSchoolMarkers === "function") {
        const originalRenderSchoolMarkers = renderSchoolMarkers;
        renderSchoolMarkers = function (schools) {
            originalRenderSchoolMarkers(schools);
            schoolMarkers.forEach((marker) => marker.off("mouseover"));
        };
    }

    if (typeof renderNinkagaiMarkers === "function") {
        const originalRenderNinkagaiMarkers = renderNinkagaiMarkers;
        renderNinkagaiMarkers = function (schools) {
            originalRenderNinkagaiMarkers(schools);
            ninkagaiMarkers.forEach((marker) => marker.off("mouseover"));
        };
    }

    if (typeof map !== "undefined") {
        map.on("movestart zoomstart", () => map.closePopup());
    }

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && typeof map !== "undefined") {
            map.closePopup();
        }
    });

    const subtitle = document.getElementById("panel-subtitle");
    if (subtitle) {
        subtitle.title = "Marker hover shows a compact tooltip; click opens details.";
    }

    // Persist favorites/viewed on the local Home Hunter server instead of tying
    // them to one browser profile. Existing localStorage data is migrated once.
    const LEGACY_VIEWED_KEY = "homehunter_viewed_v1";
    const LEGACY_FAVED_KEY = "homehunter_faved_v1";

    const persistMapState = async () => {
        const response = await fetch("/api/map-state", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                favorites: [...favedSet],
                viewed: [...viewedSet],
            }),
        });
        if (!response.ok) {
            throw new Error(`map state save failed (${response.status})`);
        }
    };

    let saveQueue = Promise.resolve();
    const saveMapState = () => {
        saveQueue = saveQueue
            .then(persistMapState)
            .catch((error) => {
                console.warn("Could not save map state:", error);
            });
    };

    if (typeof markViewed === "function") {
        markViewed = function (id) {
            if (viewedSet.has(id)) return;
            viewedSet.add(id);
            updateViewedStat();
            saveMapState();
        };
    }

    if (typeof clearViewed === "function") {
        clearViewed = function () {
            viewedSet.clear();
            updateViewedStat();
            setFilter(filterMode);
            saveMapState();
        };
    }

    if (typeof toggleFav === "function") {
        toggleFav = function (id) {
            if (favedSet.has(id)) favedSet.delete(id);
            else favedSet.add(id);
            updateFavStat();
            saveMapState();
        };
    }

    if (typeof clearFaved === "function") {
        clearFaved = function () {
            favedSet.clear();
            updateFavStat();
            setFilter(filterMode);
            saveMapState();
        };
    }

    const loadMapState = async () => {
        const hasLegacyState =
            localStorage.getItem(LEGACY_VIEWED_KEY) !== null ||
            localStorage.getItem(LEGACY_FAVED_KEY) !== null;

        try {
            const response = await fetch("/api/map-state", { cache: "no-store" });
            if (!response.ok) {
                throw new Error(`map state load failed (${response.status})`);
            }

            const state = await response.json();

            if (state.initialized) {
                // Once the local file exists, it is the source of truth.
                // This prevents stale data from an old browser from coming back.
                viewedSet.clear();
                favedSet.clear();
                (state.viewed || []).forEach((id) => viewedSet.add(id));
                (state.favorites || []).forEach((id) => favedSet.add(id));
                localStorage.removeItem(LEGACY_VIEWED_KEY);
                localStorage.removeItem(LEGACY_FAVED_KEY);
            } else if (hasLegacyState) {
                // First run after this change: migrate the current browser once.
                await persistMapState();
                localStorage.removeItem(LEGACY_VIEWED_KEY);
                localStorage.removeItem(LEGACY_FAVED_KEY);
            }

            updateViewedStat();
            updateFavStat();
            setFilter(filterMode);
        } catch (error) {
            // Keep the already-loaded legacy localStorage state as a fallback.
            console.warn("Could not load map state:", error);
        }
    };

    void loadMapState();
})();
