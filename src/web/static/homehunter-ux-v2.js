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
})();
