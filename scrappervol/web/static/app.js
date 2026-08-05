/* Sélection d'un aéroport dans la liste de suggestions.
 *
 * Les suggestions sont produites par le serveur (htmx les injecte sous le champ) ; ce script ne
 * s'occupe que du geste de sélection : choisir avec la souris ou au clavier, et refermer. Tout
 * passe par délégation sur `document`, car la liste est remplacée à chaque frappe et des
 * écouteurs posés sur ses éléments disparaîtraient avec elle.
 */
(function () {
  "use strict";

  var ACTIVE = "suggestion-active";

  function boite(champ) {
    return document.getElementById("suggestions-" + champ);
  }

  function fermer(champ) {
    var b = boite(champ);
    if (b) b.replaceChildren();
    var saisie = document.getElementById(champ);
    if (saisie) saisie.setAttribute("aria-expanded", "false");
  }

  /* Pose le code retenu dans le champ et rappelle en clair de quel aéroport il s'agit : « YUL »
   * seul, relu trois jours plus tard, ne dit plus grand-chose.
   *
   * Un champ à codes multiples (« YUL, YQB ») ne voit remplacer que le dernier code, celui en
   * cours de frappe : les précédents ont déjà été choisis. */
  function choisir(bouton) {
    var champ = bouton.dataset.cible;
    var saisie = document.getElementById(champ);
    if (!saisie) return;

    if (saisie.hasAttribute("data-multiple")) {
      var deja = saisie.value.split(",").slice(0, -1).map(function (c) {
        return c.trim();
      });
      deja.push(bouton.dataset.code);
      saisie.value = deja.join(", ");
    } else {
      saisie.value = bouton.dataset.code;
    }

    var rappel = document.getElementById("choix-" + champ);
    if (rappel) rappel.textContent = bouton.dataset.libelle;
    fermer(champ);
    saisie.focus();
  }

  function options(champ) {
    var b = boite(champ);
    return b ? Array.prototype.slice.call(b.querySelectorAll(".suggestion")) : [];
  }

  function deplacer(champ, pas) {
    var liste = options(champ);
    if (!liste.length) return;
    var index = liste.findIndex(function (o) {
      return o.classList.contains(ACTIVE);
    });
    liste.forEach(function (o) {
      o.classList.remove(ACTIVE);
    });
    var suivant = (index + pas + liste.length + 1) % (liste.length + 1);
    if (suivant < liste.length) {
      liste[suivant].classList.add(ACTIVE);
      liste[suivant].scrollIntoView({ block: "nearest" });
    }
  }

  /* Une réponse partie avant la fermeture arrive après elle et rouvrirait une liste que
   * l'utilisateur vient d'écarter. Comme des suggestions n'ont de sens que pour le champ en
   * cours de saisie, on refuse l'injection dès que celui-ci a perdu le focus. */
  document.body.addEventListener("htmx:beforeSwap", function (e) {
    var boite = e.detail.target;
    if (!boite || !boite.classList.contains("boite-suggestions")) return;
    var saisie = document.getElementById(boite.id.replace("suggestions-", ""));
    if (document.activeElement !== saisie) e.detail.shouldSwap = false;
  });

  /* Tient `aria-expanded` à jour : c'est ce qui signale l'ouverture aux lecteurs d'écran. */
  document.body.addEventListener("htmx:afterSwap", function (e) {
    var boite = e.detail.target;
    if (!boite || !boite.classList.contains("boite-suggestions")) return;
    var saisie = document.getElementById(boite.id.replace("suggestions-", ""));
    if (saisie) saisie.setAttribute("aria-expanded", boite.children.length > 0);
  });

  document.addEventListener("click", function (e) {
    var bouton = e.target.closest(".suggestion");
    if (bouton) {
      e.preventDefault();
      choisir(bouton);
      return;
    }
    // Un clic ailleurs referme toute liste ouverte.
    if (!e.target.closest(".champ-aeroport")) {
      document.querySelectorAll(".boite-suggestions").forEach(function (b) {
        fermer(b.id.replace("suggestions-", ""));
      });
    }
  });

  document.addEventListener("keydown", function (e) {
    var saisie = e.target.closest("input[data-aeroport]");
    if (!saisie) return;
    var champ = saisie.id;

    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (options(champ).length) {
        e.preventDefault();
        deplacer(champ, e.key === "ArrowDown" ? 1 : -1);
      }
    } else if (e.key === "Enter") {
      var actif = boite(champ) && boite(champ).querySelector("." + ACTIVE);
      if (actif) {
        // Entrée valide la suggestion en cours, elle ne soumet pas encore la recherche.
        e.preventDefault();
        choisir(actif);
      }
    } else if (e.key === "Escape") {
      fermer(champ);
    }
  });

  /* Une saisie modifiée à la main invalide le rappel affiché : il décrirait un autre aéroport. */
  document.addEventListener("input", function (e) {
    var saisie = e.target.closest("input[data-aeroport]");
    if (!saisie) return;
    var rappel = document.getElementById("choix-" + saisie.id);
    if (rappel) rappel.textContent = "";
  });
})();
