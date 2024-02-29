import torch
import os
#import wandb
import numpy as np
from PIL import Image
import torchvision.utils as vutils
import torchvision
from torchvision import datasets, transforms
from torch.utils.data import DataLoader
from tqdm import trange
from experiments.svhn_experiments.svhn_vae.vae.model import ConvVAE
from experiments.svhn_experiments.svhn_classifier.model import VGGNet

#run = wandb.init(project="Sinvad_latent_beased_SVHN")
def save_image(tensor, filename):
    """
    Save a tensor as an image
    """
    img = vutils.make_grid(tensor, normalize=True)  # Normalize ensures [0,1] range
    img = img.mul(255).add_(0.5).clamp_(0, 255).permute(1, 2, 0)
    # Now, transfer the tensor to CPU and convert it to numpy array for saving
    img = img.to('cpu', torch.uint8).numpy()  # Convert tensor to numpy array in [0,255] range
    img = Image.fromarray(img)
    img.save(filename)

# Set device
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
image_size = 32 * 32 * 3
# Load the trained models
vae = ConvVAE(img_size=(32, 32), c_num=3, h_dim=4000, z_dim=800).to(device)
classifier = VGGNet().to(device)
vae.load_state_dict(
    torch.load(
        "/home/maryam/Documents/SEDL/SINVAD/experiments/svhn_experiments/svhn_vae/vae/svhn_convend.pth",
        map_location=device,
    )
)
classifier.load_state_dict(
    torch.load(
        "/home/maryam/Documents/SEDL/SINVAD/experiments/svhn_experiments/svhn_classifier/model/SVHN_vggnet.pth",
        map_location=device,
    )
)
# Set models to evaluation mode
vae.eval()
classifier.eval()
result_dir = "./result_svhn_vae"
os.makedirs(result_dir, exist_ok=True)

test_dataset = torchvision.datasets.SVHN(root='./data', split="test", transform=transforms.ToTensor(), download=True)
test_data_loader = torch.utils.data.DataLoader(dataset=test_dataset, batch_size=1, shuffle=True)
print("Data loader ready...")



def calculate_fitness(logit, label):
    expected_logit = logit[label]
    # Select the two best indices [perturbed logit]
    best_indices = np.argsort(-logit)[:2]
    best_index1, best_index2 = best_indices
    if best_index1 == label:
        best_but_not_expected = best_index2
    else:
        best_but_not_expected = best_index1
    new_logit = logit[best_but_not_expected]
    # Compute fitness value
    fitness = expected_logit - new_logit
    return fitness


image_info = []
predictions = []
num_samples = 1
gen_num = 500
pop_size = 25
best_left = 10
imgs_to_samp = 100
perturbation_size = 0.1 # Default perturbation size
initial_perturbation_size = 0.7 # Initial perturbation size

all_img_lst = []
### multi-image sample loop ###
for img_idx in trange(imgs_to_samp):
    for i, (x, x_class) in enumerate(test_data_loader):
        samp_img = x[0:1]
        samp_class = x_class[0].item()


    img_enc, _ = vae.encode(samp_img.to(device))

    # img_enc, _ = vae.encode(samp_img.view(-1, image_size).to(device))
    original_lv = img_enc
    original_image = vae.decode(original_lv).view(-1, 3, 32, 32)
    original_logit = classifier(original_image).squeeze().detach().cpu().numpy()
    original_label = original_logit.argmax().item()
    # Calculate fitness for the original image
    fitness_original = calculate_fitness(original_logit, original_label)
    all_img_lst = []

    ### Initialize optimization ###
    init_pop = [
        original_lv + initial_perturbation_size * torch.randn(1, 800).to(device)
        for _ in range(pop_size)
    ]
    binom_sampler = torch.distributions.binomial.Binomial(
        probs=0.5 * torch.ones(original_lv.size())
    )
    now_pop = init_pop
    prev_best = np.inf
    ### GA ###
    for g_idx in range(gen_num):
        indivs = torch.cat(now_pop, dim=0)
        dec_imgs = vae.decode(indivs).view(-1, 3, 32, 32)
        all_logits = classifier(dec_imgs).squeeze().detach().cpu().numpy()

        fitness_scores = [
            calculate_fitness(all_logits[k_idx], original_label)
            for k_idx in range(pop_size)
        ]

        # Perform selection
        best_idxs = sorted(
            range(len(fitness_scores)),
            key=lambda i_x: fitness_scores[i_x],
            reverse=True,
        )[-best_left:]
        # Consider the lowest fitness score
        now_best = np.min(fitness_scores)
        parent_pop = [now_pop[idx] for idx in best_idxs]

        # Perform crossover and mutation
        print(
            "now_best "
            + str(now_best)
            + ", Average_score "
            + str(np.mean(fitness_scores))
        )
        # Log fitness scores
        #wandb.log({"Fitness Score": now_best, "Sample Index": i, "Iteration": g_idx})
        if now_best < 0:
            break
        elif now_best == prev_best:
            perturbation_size *= 2
        else:
            perturbation_size = initial_perturbation_size

        k_pop = []
        for k_idx in range(pop_size - best_left):
            mom_idx, pop_idx = np.random.choice(best_left, size=2, replace=False)
            spl_idx = np.random.choice(800, size=1)[0]
            k_gene = torch.cat(
                [parent_pop[mom_idx][:, :spl_idx], parent_pop[pop_idx][:, spl_idx:]],
                dim=1,
            )  # crossover

            # Mutation
            diffs = (k_gene != original_lv).float()
            k_gene += (
                perturbation_size * torch.randn(k_gene.size()) * diffs
            )  # random adding noise only to diff places
            # random matching to latent_images[i]
            interp_mask = binom_sampler.sample()
            k_gene = interp_mask * original_lv + (1 - interp_mask) * k_gene
            k_pop.append(k_gene)

        # Current population is obtained combining the parent pop and its offspring
        now_pop = parent_pop + k_pop
        prev_best = now_best

    mod_best = parent_pop[-1].clone()
    final_bound_img = vae.decode(parent_pop[-1]).detach().numpy()
    all_img_lst.append(final_bound_img)
    # Convert the image to a PyTorch tensor
    final_bound_img_tensor = torch.from_numpy(final_bound_img).float().to(device)
    prediction = torch.argmax(classifier(final_bound_img_tensor)).item()
    predictions.append(prediction)


    # Transpose the dimensions to (32, 32, 3)
   # transposed_img_data = final_bound_img[0].transpose(1, 2, 0)

    # Scale the image data from [0, 1] to [0, 255]
    #scaled_img_data = (transposed_img_data * 255).astype(np.uint8)

    # Create the PIL Image
    #image = Image.fromarray(scaled_img_data, mode="RGB")
    #wandb_image = wandb.Image(image)

    # Log the image along with other relevant information
    #wandb.log(
    #   {
    #        "Generated Image": wandb_image,
    #       "Expected Label X": original_label,
    #        "Predicted Label Y": predictions,
    #    }
    #)

    save_image(final_bound_img_tensor, os.path.join(result_dir, f'image_{img_idx}_X{original_label}_Y{prediction}.png'))
    # Store the image info
    image_info.append((img_idx, original_label, prediction))

# Save all generated images as a numpy array
all_imgs = np.vstack(all_img_lst)
np.save("generated_images_vae_svhn.npy", all_imgs)
# Save the image info
with open(os.path.join(result_dir, 'image_info.txt'), 'w') as f:
    f.write("Image Index, Expected Label X, Predicted Label Y\n")
    for img_info in image_info:
        f.write(f"{img_info[0]}, {img_info[1]}, {img_info[2]}\n")
misclassified_count = 0

# Iterate over the image info list
for img_info in image_info:
    expected_label = img_info[1]
    predicted_label = img_info[2]
    if predicted_label != expected_label:
        misclassified_count += 1

misclassification_percentage = (misclassified_count / len(image_info)) * 100

print(f"Misclassification Percentage: {misclassification_percentage:.2f}%")
