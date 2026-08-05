/* Calendrier de plage : l'aller et le retour se choisissent dans un même panneau.
 *
 * Il remplace `<input type="date">`, dont le sélecteur appartient au navigateur — impossible à
 * mettre au ton du reste, et différent sur chaque machine. Deux mois sont affichés côte à côte
 * parce qu'un aller-retour enjambe souvent la fin d'un mois.
 *
 * Chaque champ garde un `<input type="hidden">` au format ISO : le serveur reçoit exactement ce
 * qu'il recevait avant, seule la saisie change.
 */
(function () {
  "use strict";

  var JOURS = ["dim", "lun", "mar", "mer", "jeu", "ven", "sam"];
  var MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
              "septembre", "octobre", "novembre", "décembre"];

  /* Les dates se manipulent en clair, jamais via l'horodatage : `new Date("2026-08-20")` est lu
   * en UTC et recule d'un jour à l'ouest de Greenwich — le 20 août devient le 19. */
  function iso(d) {
    var m = String(d.getMonth() + 1).padStart(2, "0");
    var j = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + m + "-" + j;
  }

  function depuisIso(texte) {
    if (!texte) return null;
    var p = texte.split("-");
    if (p.length !== 3) return null;
    var d = new Date(+p[0], +p[1] - 1, +p[2]);
    return isNaN(d.getTime()) ? null : d;
  }

  function enClair(d) {
    return JOURS[d.getDay()] + " " + d.getDate() + " " + MOIS[d.getMonth()] + " " + d.getFullYear();
  }

  function memeJour(a, b) {
    return a && b && iso(a) === iso(b);
  }

  function ajouterMois(d, n) {
    return new Date(d.getFullYear(), d.getMonth() + n, 1);
  }

  /* ------------------------------------------------------------------ état */

  function Plage(bloc) {
    this.bloc = bloc;
    this.debutCache = bloc.querySelector("[data-role=debut-valeur]");
    this.finCache = bloc.querySelector("[data-role=fin-valeur]");
    this.debutVisible = bloc.querySelector("[data-role=debut-visible]");
    this.finVisible = bloc.querySelector("[data-role=fin-visible]");
    this.panneau = bloc.querySelector("[data-role=panneau]");
    this.min = depuisIso(bloc.dataset.min) || null;
    this.debut = depuisIso(this.debutCache.value);
    this.fin = this.finCache ? depuisIso(this.finCache.value) : null;
    this.survol = null;
    this.ouvert = false;
    this.boutons = [];
    // Le champ cliqué décide de ce qu'on est en train de choisir.
    this.cible = "debut";
    this.mois = this.debut ? new Date(this.debut.getFullYear(), this.debut.getMonth(), 1)
                           : new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  }

  Plage.prototype.avantMin = function (d) {
    return this.min && iso(d) < iso(this.min);
  };

  Plage.prototype.ecrire = function () {
    this.debutCache.value = this.debut ? iso(this.debut) : "";
    this.debutVisible.value = this.debut ? enClair(this.debut) : "";
    if (this.finCache) {
      this.finCache.value = this.fin ? iso(this.fin) : "";
      this.finVisible.value = this.fin ? enClair(this.fin) : "";
    }
  };

  Plage.prototype.choisir = function (d) {
    if (this.avantMin(d)) return;

    if (!this.finCache) {
      this.debut = d;
      this.ecrire();
      this.fermer();
      return;
    }

    // Un clic sur « retour » alors qu'un aller existe complète la plage ; sinon on repart du
    // début. Une fin antérieure au début redevient un début : c'est ce que la personne montre.
    if (this.cible === "fin" && this.debut && iso(d) >= iso(this.debut)) {
      this.fin = d;
    } else if (this.cible === "debut" || !this.debut || iso(d) < iso(this.debut)) {
      this.debut = d;
      this.fin = null;
      this.cible = "fin";
    } else {
      this.fin = d;
    }

    this.ecrire();
    if (this.debut && this.fin) this.fermer();
    else this.peindre();
  };

  /* --------------------------------------------------------------- rendu */

  /* Recolore les jours sans toucher à l'arbre.
   *
   * Reconstruire le panneau au survol détacherait le bouton visé entre le survol et le clic : le
   * clic tomberait dans le vide. Seules les classes changent, les nœuds restent. */
  Plage.prototype.peindre = function () {
    var self = this;
    var borne = this.fin || (this.debut && this.cible === "fin" ? this.survol : null);

    this.boutons.forEach(function (paire) {
      var d = paire[0];
      var b = paire[1];
      var classes = ["cal-jour"];
      if (self.avantMin(d)) classes.push("cal-jour-hors");
      if (memeJour(d, self.debut)) classes.push("cal-jour-debut");
      if (memeJour(d, self.fin)) classes.push("cal-jour-fin");
      if (self.debut && borne && iso(d) > iso(self.debut) && iso(d) < iso(borne)) {
        classes.push("cal-jour-entre");
      }
      b.className = classes.join(" ");
    });

    var aide = this.panneau.querySelector(".cal-aide");
    if (aide) {
      aide.textContent = this.debut && !this.fin ? "Choisissez la date de retour."
                                                 : "Choisissez la date d'aller.";
    }
  };

  Plage.prototype.rendre = function () {
    var panneau = this.panneau;
    panneau.replaceChildren();
    this.boutons = [];

    var nav = document.createElement("div");
    nav.className = "cal-nav";
    var self = this;

    function fleche(texte, pas, etiquette) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "cal-fleche";
      b.textContent = texte;
      b.setAttribute("aria-label", etiquette);
      b.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        self.mois = ajouterMois(self.mois, pas);
        self.rendre();
      });
      return b;
    }

    var titre = document.createElement("div");
    titre.className = "cal-titre";
    var second = ajouterMois(this.mois, 1);
    titre.textContent = MOIS[this.mois.getMonth()] + " " + this.mois.getFullYear() +
      " — " + MOIS[second.getMonth()] + " " + second.getFullYear();

    nav.append(fleche("‹", -1, "Mois précédent"), titre, fleche("›", 1, "Mois suivant"));
    panneau.append(nav);

    var grille = document.createElement("div");
    grille.className = "cal-mois";
    grille.append(this.rendreMois(this.mois), this.rendreMois(second));
    panneau.append(grille);

    if (this.finCache) {
      var aide = document.createElement("p");
      aide.className = "cal-aide discret";
      panneau.append(aide);
    }

    this.peindre();
  };

  Plage.prototype.rendreMois = function (premier) {
    var self = this;
    var table = document.createElement("table");
    table.className = "cal-table";

    var thead = document.createElement("thead");
    var ligneJours = document.createElement("tr");
    JOURS.forEach(function (j) {
      var th = document.createElement("th");
      th.scope = "col";
      th.textContent = j[0].toUpperCase();
      th.title = j;
      ligneJours.append(th);
    });
    thead.append(ligneJours);
    table.append(thead);

    var corps = document.createElement("tbody");
    var ligne = document.createElement("tr");
    // La semaine commence le dimanche, usage courant au Canada.
    for (var vide = 0; vide < premier.getDay(); vide++) ligne.append(document.createElement("td"));

    var dernier = new Date(premier.getFullYear(), premier.getMonth() + 1, 0).getDate();
    for (var jour = 1; jour <= dernier; jour++) {
      var d = new Date(premier.getFullYear(), premier.getMonth(), jour);
      if (ligne.children.length === 7) {
        corps.append(ligne);
        ligne = document.createElement("tr");
      }
      ligne.append(this.rendreJour(d, self));
    }
    corps.append(ligne);
    table.append(corps);
    return table;
  };

  Plage.prototype.rendreJour = function (d, self) {
    var td = document.createElement("td");
    var b = document.createElement("button");
    b.type = "button";
    b.className = "cal-jour";
    b.textContent = String(d.getDate());
    b.dataset.date = iso(d);

    if (this.avantMin(d)) b.disabled = true;

    b.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      self.choisir(d);
    });
    // Aperçu de la plage pendant qu'on cherche la date de retour.
    b.addEventListener("mouseenter", function () {
      if (self.debut && !self.fin && self.cible === "fin") {
        self.survol = d;
        self.peindre();
      }
    });

    this.boutons.push([d, b]);
    td.append(b);
    return td;
  };

  /* ------------------------------------------------------- ouvrir/fermer */

  Plage.prototype.ouvrir = function (cible) {
    fermerTout(this);
    this.cible = cible;
    // S'ouvrir sur le mois de la date déjà choisie plutôt que sur le mois courant.
    var ancre = (cible === "fin" ? this.fin || this.debut : this.debut) || this.min;
    if (ancre) this.mois = new Date(ancre.getFullYear(), ancre.getMonth(), 1);
    this.ouvert = true;
    this.panneau.hidden = false;
    this.bloc.classList.add("champ-date-ouvert");
    this.rendre();
  };

  Plage.prototype.fermer = function () {
    this.ouvert = false;
    this.survol = null;
    this.panneau.hidden = true;
    this.bloc.classList.remove("champ-date-ouvert");
  };

  var plages = [];

  function fermerTout(sauf) {
    plages.forEach(function (p) {
      if (p !== sauf && p.ouvert) p.fermer();
    });
  }

  function installer() {
    document.querySelectorAll("[data-plage-dates]").forEach(function (bloc) {
      if (bloc.dataset.installe) return;
      bloc.dataset.installe = "1";
      var plage = new Plage(bloc);
      plages.push(plage);
      plage.ecrire();

      [["debut", plage.debutVisible], ["fin", plage.finVisible]].forEach(function (paire) {
        var role = paire[0];
        var champ = paire[1];
        if (!champ) return;
        champ.addEventListener("click", function () {
          plage.ouvert && plage.cible === role ? plage.fermer() : plage.ouvrir(role);
        });
        champ.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            plage.ouvrir(role);
          } else if (e.key === "Escape") {
            plage.fermer();
          }
        });
      });
    });
  }

  document.addEventListener("click", function (e) {
    if (!e.target.closest("[data-plage-dates]")) fermerTout(null);
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") fermerTout(null);
  });

  document.addEventListener("DOMContentLoaded", installer);
  // Les champs de dates d'un trajet sont réinjectés par htmx quand la politique change.
  document.body.addEventListener("htmx:afterSwap", installer);
  installer();
})();
