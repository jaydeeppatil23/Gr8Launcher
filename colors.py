"""
Minecraft Launcher Theme Tokens
Matte, high-contrast, premium dark mode palettes.
"""

THEMES = {
    # 1. Redstone (Burnt crimson base, unlit wire surfaces, energized ruby accents)
    "Redstone": {
        "bg": "#0F0B0C",
        "surface": "#191113",
        "card": "#24171A",
        "card_hover": "#301E22",
        "border": "#3D2227",
        
        "btn_primary": "#A6212D",
        "btn_primary_hover": "#BC2937",
        "btn_primary_active": "#8E1B25",
        "btn_primary_text": "#FFFFFF",
        
        "btn_secondary": "#211618",
        "btn_secondary_hover": "#2B1D20",
        "btn_secondary_active": "#181012",
        "btn_secondary_text": "#F7EDED",
        
        "text_primary": "#F7EDED",
        "text_secondary": "#B89DA1",
        "text_muted": "#6E5458",
        "text_dark": "#453336",
    },

    # 2. Sculk (Abyssal blue-black, deep marine surfaces, muted cyan-teal accents)
    "Sculk": {
        "bg": "#090D12",
        "surface": "#0F161E",
        "card": "#15202B",
        "card_hover": "#1C2A38",
        "border": "#1E3040",
        
        "btn_primary": "#17899C",
        "btn_primary_hover": "#1C9CB2",
        "btn_primary_active": "#137282",
        "btn_primary_text": "#090D12",
        
        "btn_secondary": "#15212E",
        "btn_secondary_hover": "#1C2C3D",
        "btn_secondary_active": "#101924",
        "btn_secondary_text": "#EEF7F8",
        
        "text_primary": "#EEF7F8",
        "text_secondary": "#8AA4B0",
        "text_muted": "#556E7A",
        "text_dark": "#354750",
    },

    # 3. Netherite renamed to Gold (Smoky obsidian base, basalt surfaces, warm raw-gold accents)
    "Gold": {
        "bg": "#0F0E0E",
        "surface": "#171515",
        "card": "#211E1F",
        "card_hover": "#2A2728",
        "border": "#332F30",
        
        "btn_primary": "#C68038",
        "btn_primary_hover": "#D88F43",
        "btn_primary_active": "#AC6D2C",
        "btn_primary_text": "#1A1005",
        
        "btn_secondary": "#221E1E",
        "btn_secondary_hover": "#2C2727",
        "btn_secondary_active": "#1B1717",
        "btn_secondary_text": "#F5F2F0",
        
        "text_primary": "#F5F2F0",
        "text_secondary": "#A89E99",
        "text_muted": "#69615D",
        "text_dark": "#443F3D",
    },

    # 4. Lush (Dark peat base, muted foliage surfaces, jade emerald accents)
    "Lush": {
        "bg": "#0B0E0C",
        "surface": "#121714",
        "card": "#1A221D",
        "card_hover": "#222C26",
        "border": "#28362D",
        
        "btn_primary": "#2D8354",
        "btn_primary_hover": "#349661",
        "btn_primary_active": "#246C44",
        "btn_primary_text": "#FFFFFF",
        
        "btn_secondary": "#17211B",
        "btn_secondary_hover": "#202D25",
        "btn_secondary_active": "#121A15",
        "btn_secondary_text": "#EFF5F1",
        
        "text_primary": "#EFF5F1",
        "text_secondary": "#95A89B",
        "text_muted": "#5C6E62",
        "text_dark": "#3B473F",
    },

    # 5. Amethyst (Smooth basalt violet base with rich crystal-purple accents)
    "Amethyst": {
        "bg": "#0D0A13",              # Deep geode mantle black-violet
        "surface": "#15101F",         # Calcite/amethyst outer shell
        "card": "#1D162B",            # Polished amethyst block tint
        "card_hover": "#271E3A",      # Soft crystal facet shine
        "border": "#36294F",          # Subtle geode fissure edge
        
        "btn_primary": "#7F4DB8",     # Rich mineral violet
        "btn_primary_hover": "#8E5AC9",
        "btn_primary_active": "#6C3F9E",
        "btn_primary_text": "#FFFFFF",
        
        "btn_secondary": "#1E172B",
        "btn_secondary_hover": "#281F38",
        "btn_secondary_active": "#171221",
        "btn_secondary_text": "#F4F0FA",
        
        "text_primary": "#F4F0FA",    # Pale lilac white
        "text_secondary": "#AFA3C7",  # Muted amethyst dust
        "text_muted": "#6E6382",      # Inactive elements
        "text_dark": "#433A52",       # Watermarks / borders
    },

    # 6. Cherry Blossom (Charcoal bark base with matte petal-rose accents)
    "Cherry Blossom": {
        "bg": "#120D10",              # Dark cherry log bark with faint mauve undertone
        "surface": "#1B1317",         # Deep timber layer
        "card": "#271B21",            # Shaded petal ground
        "card_hover": "#34242C",      # Soft blossom warmth
        "border": "#432C37",          # Bark fissure outline
        
        "btn_primary": "#C95F85",     # Velvety rose petal (rich and muted, not neon pink)
        "btn_primary_hover": "#D86E94",
        "btn_primary_active": "#B14F73",
        "btn_primary_text": "#120D10",
        
        "btn_secondary": "#22171D",
        "btn_secondary_hover": "#2C1E26",
        "btn_secondary_active": "#1A1116",
        "btn_secondary_text": "#F9EFF3",
        
        "text_primary": "#F9EFF3",    # Clean warm petal white
        "text_secondary": "#BEA3AF",  # Dusty blossom rose
        "text_muted": "#755F6A",      # Muted branch tone
        "text_dark": "#473840",       # Dark accents / inactive
    },

# 7.1. Earth (Deep soil and shadow-forest base with vibrant grass-green accents)
    "Earth 1": {
        "bg": "#0A0D08",              # Very dark loamy earth
        "surface": "#11160D",         # Deep mossy shadow
        "card": "#1A2114",            # Forest floor
        "card_hover": "#232C1B",      # Light hitting foliage
        "border": "#2E3A24",          # Earthy contour
        
        "btn_primary": "#46BA34",     # The requested vibrant nature green
        "btn_primary_hover": "#54C842",
        "btn_primary_active": "#3A9E2A",
        "btn_primary_text": "#0A0D08",
        
        "btn_secondary": "#182113",
        "btn_secondary_hover": "#202B19",
        "btn_secondary_active": "#11180D",
        "btn_secondary_text": "#F1F7EE",
        
        "text_primary": "#F1F7EE",    #  Crisp, pale botanical white
        "text_secondary": "#A7B4A0",  # "#A3B897", Muted sage/fern
        "text_muted": "#68785E",      # Inactive elements
        "text_dark": "#404B39",       # Watermarks / borders
    },
    # 7.2. Earth (Deep warm soil base, rich loam surfaces, vibrant grass-green accents)
    "Earth 2": {
        "bg": "#0E0C0A",              # Deep dark soil / night forest floor
        "surface": "#161310",         # Rich dark dirt layer
        "card": "#1F1B17",            # Warm earthy brown base
        "card_hover": "#2B251F",      # Soft ambient light on soil
        "border": "#3A322A",          # Subtle root/branch outline
        
        "btn_primary": "#46BA34",     # Vibrant grass/leaf green
        "btn_primary_hover": "#54CC41",
        "btn_primary_active": "#399C29",
        "btn_primary_text": "#0E0C0A",
        
        "btn_secondary": "#1E1A17",
        "btn_secondary_hover": "#28231E",
        "btn_secondary_active": "#161311",
        "btn_secondary_text": "#F6F4F2",
        
        "text_primary": "#F6F4F2",    # Clean, warm earthy white
        "text_secondary": "#B3A9A0",  # Muted clay/stone tone
        "text_muted": "#736B63",      # Inactive dirt elements
        "text_dark": "#4A433D",       # Dark accents / shadows
    },
}

DEFAULT_THEME = "Earth 2"


def get_theme(name: str) -> dict:
    """Fetch color scheme dictionary by name with automatic fallback."""
    return THEMES.get(name, THEMES[DEFAULT_THEME])

def get_pagecolor():
    return "#050505"

def get_theme_names() -> list[str]:
    """Get list of available theme names for UI menus."""
    return list(THEMES.keys())